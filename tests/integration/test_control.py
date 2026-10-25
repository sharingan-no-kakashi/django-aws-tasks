"""Real-transaction operator idempotency, intent conflicts, and ownership safety."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.db import IntegrityError, connections
from django.utils import timezone

from django_tasks_aws.exceptions import ControlConflict, ControlRejected
from django_tasks_aws.models import HoldReason, TaskControlEvent, TaskRecord, TaskStatus
from django_tasks_aws.services import claims, control, dispatch
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


@pytest.fixture(autouse=True)
def no_publication(monkeypatch):
    sender = Mock()
    monkeypatch.setattr(dispatch, "publish_task", sender)
    return sender


def request(**options):
    return control.ControlRequest(
        **dict(
            request_id="request-1",
            reason="Provider recovered",
            actor_reference="operator-1",
            **options,
        )
    )


def failed():
    return make_task_record(
        status=TaskStatus.FAILED, finished_at=timezone.now(), operation_key="invoice:42"
    )


def operate(backend, record, action, intent=None, **options):
    return control.operate(
        backend=backend,
        task_id=str(record.id),
        action=action,
        request=intent or request(),
        **options,
    )


def test_replay_preserves_operation_and_is_idempotent(test_backend):
    original = failed()
    intent = request(effects_reviewed=True)
    first = operate(test_backend, original, "replay", intent)
    second = operate(test_backend, original, "replay", intent)
    replacement = TaskRecord.objects.using("postgres").get(pk=first.replacement_task_id)
    assert replacement.operation_key == original.operation_key
    assert replacement.replay_of_id == original.id
    assert replacement.queue_name == original.queue_name
    assert second.repeated and second.replacement_task_id == first.replacement_task_id
    assert TaskControlEvent.objects.using("postgres").count() == 1
    original.refresh_from_db()
    assert original.status == TaskStatus.FAILED


def test_concurrent_replay_creates_one_replacement(test_backend):
    original = failed()

    def submit(_):
        try:
            return operate(test_backend, original, "replay", request(effects_reviewed=True))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert len({result.replacement_task_id for result in results}) == 1
    assert TaskRecord.objects.using("postgres").filter(replay_of_id=original.id).count() == 1


def test_changed_intent_conflicts(test_backend):
    original = failed()
    intent = request(effects_reviewed=True)
    operate(test_backend, original, "replay", intent)
    with pytest.raises(ControlConflict):
        operate(test_backend, original, "replay", replace(intent, queue_name="documents"))


def test_repeated_release_cannot_release_later_hold(test_backend):
    original = make_task_record(
        held_at=timezone.now(),
        hold_reason=HoldReason.DELIVERY_EXHAUSTED,
        dispatches_without_claim=12,
    )
    intent = request(reset_delivery=True)
    operate(test_backend, original, "release", intent)
    later = timezone.now() + timedelta(seconds=1)
    TaskRecord.objects.using("postgres").filter(pk=original.id).update(
        held_at=later, hold_reason=HoldReason.AMBIGUOUS_OUTCOME
    )
    assert operate(test_backend, original, "release", intent).repeated
    original.refresh_from_db()
    assert original.held_at == later


def test_release_checks_preview_hold_and_preserves_attempt_budget(test_backend):
    original = make_task_record(
        held_at=timezone.now(),
        hold_reason=HoldReason.DELIVERY_EXHAUSTED,
        dispatches_without_claim=12,
        attempts_started=2,
    )
    with pytest.raises(ControlConflict):
        operate(
            test_backend, original, "release", request(expected_hold="old", reset_delivery=True)
        )
    operate(test_backend, original, "release", request(reset_delivery=True))
    original.refresh_from_db()
    assert original.attempts_started == 2 and original.dispatches_without_claim == 0


def test_dry_run_never_writes_or_publishes(test_backend, no_publication):
    original = failed()
    result = operate(test_backend, original, "replay", request(effects_reviewed=True), dry_run=True)
    assert result.dry_run
    assert TaskRecord.objects.using("postgres").count() == 1
    assert TaskControlEvent.objects.using("postgres").count() == 0
    no_publication.assert_not_called()


def test_replay_requires_effect_review(test_backend):
    with pytest.raises(ControlRejected, match="review"):
        operate(test_backend, failed(), "replay")


def test_ambiguous_hold_requires_effect_review(test_backend):
    original = make_task_record(held_at=timezone.now(), hold_reason=HoldReason.AMBIGUOUS_OUTCOME)
    with pytest.raises(ControlRejected, match="review"):
        operate(test_backend, original, "release")


def test_live_attempt_cannot_be_recovered_or_replayed(test_backend):
    original = make_task_record()
    claims.claim_task(
        task_id=str(original.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    for action in ("reconcile", "replay", "release", "dispatch"):
        with pytest.raises(ControlRejected):
            operate(test_backend, original, action, request(effects_reviewed=True))
    original.refresh_from_db()
    assert original.status == TaskStatus.RUNNING


def test_expired_recovery_is_audited(test_backend):
    original = make_task_record()
    claims.claim_task(
        task_id=str(original.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    TaskRecord.objects.using("postgres").filter(pk=original.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    operate(test_backend, original, "reconcile")
    original.refresh_from_db()
    assert original.held_at and original.hold_reason == HoldReason.AMBIGUOUS_OUTCOME
    assert TaskControlEvent.objects.using("postgres").get().action == "reconcile"


def test_audit_failure_rolls_back_replacement(test_backend, monkeypatch, no_publication):
    original = failed()

    def broken_audit(*args, **kwargs):
        raise IntegrityError("audit unavailable")

    monkeypatch.setattr(control, "audit", broken_audit)
    with pytest.raises(IntegrityError):
        operate(test_backend, original, "replay", request(effects_reviewed=True))
    assert not TaskRecord.objects.using("postgres").filter(replay_of_id=original.id).exists()
    no_publication.assert_not_called()


def test_publication_failure_does_not_erase_accepted_control(test_backend, no_publication):
    no_publication.side_effect = RuntimeError("queue unavailable")
    original = failed()
    result = operate(test_backend, original, "replay", request(effects_reviewed=True))
    assert TaskRecord.objects.using("postgres").filter(pk=result.replacement_task_id).exists()
    assert TaskControlEvent.objects.using("postgres").count() == 1


def test_release_preview_requires_explicit_delivery_reset(test_backend):
    original = make_task_record(
        held_at=timezone.now(),
        hold_reason=HoldReason.DELIVERY_EXHAUSTED,
        dispatches_without_claim=12,
    )
    with pytest.raises(ControlRejected, match="reset"):
        operate(test_backend, original, "release", dry_run=True)


def test_bulk_controls_are_independently_idempotent_per_target(test_backend):
    originals = [failed(), failed()]
    intent = request(effects_reviewed=True)
    results = [operate(test_backend, record, "replay", intent) for record in originals]
    assert len({result.replacement_task_id for result in results}) == 2
    assert TaskControlEvent.objects.using("postgres").count() == 2


def test_replay_after_expiry_is_refused(test_backend):
    original = failed()
    TaskRecord.objects.using("postgres").filter(pk=original.id).update(
        finished_at=timezone.now() - timedelta(days=31)
    )
    with pytest.raises(ControlRejected, match="expired"):
        operate(test_backend, original, "replay", request(effects_reviewed=True))


def test_operator_recovery_races_automatic_reaper_safely(test_backend):
    from threading import Barrier

    from django_tasks_aws.services.reconcile import recover_one

    original = make_task_record()
    claim = claims.claim_task(
        task_id=str(original.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    TaskRecord.objects.using("postgres").filter(pk=original.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    barrier = Barrier(2)

    def recover(operator):
        try:
            barrier.wait(timeout=3)
            if operator:
                return operate(test_backend, original, "reconcile")
            return recover_one(
                original.id, database_alias="postgres", policy_catalog=test_backend.policies
            )
        except ControlRejected:
            return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recover, (True, False)))
    original.refresh_from_db()
    assert any(results)
    assert original.attempts_started == 1 and original.lease_token is None
    assert original.attempts.get(id=claim.attempt_id).status == "ABANDONED"
    assert TaskControlEvent.objects.using("postgres").count() <= 1


def test_hold_release_races_worker_claim_without_overwriting_ownership(test_backend):
    from threading import Barrier

    original = make_task_record(
        held_at=timezone.now(),
        hold_reason=HoldReason.DELIVERY_EXHAUSTED,
        dispatches_without_claim=12,
    )
    barrier = Barrier(2)

    def run(worker):
        try:
            barrier.wait(timeout=3)
            if worker:
                return claims.claim_task(
                    task_id=str(original.id),
                    queue_name="short",
                    worker_name="short",
                    lease_seconds=90,
                    database_alias="postgres",
                )
            return operate(test_backend, original, "release", request(reset_delivery=True))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker, _ = list(pool.map(run, (True, False)))
    original.refresh_from_db()
    if worker is not None:
        assert original.status == TaskStatus.RUNNING and original.lease_token == worker.attempt_id
    else:
        assert original.status == TaskStatus.PENDING
    assert original.held_at is None and original.dispatches_without_claim == 0
