from datetime import timedelta

import pytest
from django.tasks.exceptions import TaskResultDoesNotExist
from django.utils import timezone

from django_tasks_aws import submit
from django_tasks_aws.exceptions import ExistingTaskResultExpired
from django_tasks_aws.models import HoldReason, TaskControlEvent, TaskRecord, TaskStatus
from django_tasks_aws.services import claims, control, outcomes, retention
from tests.demo_tasks import add
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def terminal(**overrides):
    return make_task_record(
        status=TaskStatus.SUCCESSFUL, finished_at=timezone.now() - timedelta(days=31), **overrides
    )


def test_redaction_preserves_identity_and_dedupe(test_backend):
    accepted = submit(add, args=(1, 2), key="retained")
    TaskRecord.objects.using("postgres").filter(pk=accepted.id).update(
        status=TaskStatus.SUCCESSFUL, finished_at=timezone.now() - timedelta(days=31), result=3
    )
    assert retention.expire_details(test_backend).redacted == 1
    record = TaskRecord.objects.using("postgres").get(pk=accepted.id)
    assert record.payload == {} and record.result is None
    assert record.dedupe_key == "retained" and record.payload_hash
    with pytest.raises(TaskResultDoesNotExist):
        test_backend.get_result(accepted.id)
    with pytest.raises(ExistingTaskResultExpired) as error:
        submit(add, args=(1, 2), key="retained")
    assert error.value.task_id == accepted.id
    assert TaskRecord.objects.using("postgres").count() == 1


def test_unfinished_and_held_work_is_never_redacted(test_backend):
    pending = make_task_record()
    held = make_task_record(held_at=timezone.now(), hold_reason=HoldReason.AMBIGUOUS_OUTCOME)
    running = make_task_record()
    claims.claim_task(
        task_id=str(running.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    TaskRecord.objects.using("postgres").update(created_at=timezone.now() - timedelta(days=365))
    assert retention.expire_details(test_backend).redacted == 0
    for record in (pending, held, running):
        record.refresh_from_db()
        assert record.payload and record.details_expired_at is None


def test_cleanup_is_bounded_idempotent_and_dry_run_safe(test_backend):
    for _ in range(3):
        terminal()
    assert retention.expire_details(test_backend, limit=2, dry_run=True).redacted == 2
    assert (
        not TaskRecord.objects.using("postgres").filter(details_expired_at__isnull=False).exists()
    )
    assert retention.expire_details(test_backend, limit=2).redacted == 2
    assert retention.expire_details(test_backend, limit=2).redacted == 1
    assert retention.expire_details(test_backend, limit=2).redacted == 0


def test_result_expiry_applies_before_cleanup(test_backend):
    record = terminal()
    with pytest.raises(TaskResultDoesNotExist):
        test_backend.get_result(str(record.id))


def test_retention_keeps_failure_history_until_failure_window(test_backend):
    record = make_task_record()
    claim = claims.claim_task(
        task_id=str(record.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    outcomes.finalize_failure(
        task_id=str(record.id),
        attempt_id=claim.attempt_id,
        error={"error_type": "builtins.ValueError", "message": "diagnostic"},
        database_alias="postgres",
    )
    TaskRecord.objects.using("postgres").filter(pk=record.id).update(
        finished_at=timezone.now() - timedelta(days=31)
    )
    retention.expire_details(test_backend)
    record.refresh_from_db()
    assert record.attempts.count() == 1 and record.last_error
    TaskRecord.objects.using("postgres").filter(pk=record.id).update(
        finished_at=timezone.now() - timedelta(days=91)
    )
    assert retention.expire_details(test_backend).diagnostics_redacted == 1
    record.refresh_from_db()
    assert record.attempts.count() == 0 and record.last_error is None


def test_audit_redaction_preserves_repeat_request_identity(test_backend, monkeypatch):
    monkeypatch.setattr(control, "dispatch_after_commit", lambda *args: None)
    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    intent = control.ControlRequest(
        "old-request", "investigated", "operator", effects_reviewed=True
    )
    first = control.operate(
        backend=test_backend, task_id=str(record.id), action="replay", request=intent
    )
    TaskControlEvent.objects.using("postgres").update(
        created_at=timezone.now() - timedelta(days=91)
    )
    assert retention.expire_details(test_backend).audit_redacted == 1
    event = TaskControlEvent.objects.using("postgres").get()
    assert event.reason == "[expired]" and event.intent_hash
    second = control.operate(
        backend=test_backend, task_id=str(record.id), action="replay", request=intent
    )
    assert second.repeated and second.replacement_task_id == first.replacement_task_id


def test_late_delivery_acknowledges_retained_terminal_identity(test_backend):
    from django_tasks_aws.schemas import WorkerIdentity
    from django_tasks_aws.services.worker import MessageDecision, handle_task

    record = terminal()
    retention.expire_details(test_backend)
    assert (
        handle_task(
            str(record.id),
            backend=test_backend,
            worker=WorkerIdentity("short", "arn"),
            remaining_seconds=60,
        )
        == MessageDecision.ACK
    )
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL and record.attempts_started == 0


def test_cleanup_skips_locked_terminal_work_and_makes_progress(test_backend):
    from django.db import connections

    first, second = terminal(), terminal()
    connection = connections["postgres"]
    import psycopg

    params = connection.get_connection_params()
    for name in ("cursor_factory", "context", "prepare_threshold"):
        params.pop(name, None)
    with psycopg.connect(**params) as blocker:
        blocker.execute("SELECT id FROM task_record WHERE id=%s FOR UPDATE", [first.id])
        report = retention.expire_details(test_backend, limit=1)
        assert report.task_ids == (str(second.id),)
    assert retention.expire_details(test_backend, limit=1).task_ids == (str(first.id),)
