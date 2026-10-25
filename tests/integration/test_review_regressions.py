"""Regressions for backend fencing, corrupt history, and database-clock eligibility."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from django_tasks_aws.exceptions import InvariantViolation
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services import claims, dispatch, invariants, outcomes, reconcile
from django_tasks_aws.services.worker import handle_task
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_reconciliation_never_consumes_another_backends_budget(test_backend, fake_transport):
    other = make_task_record(backend_alias="other")
    own = make_task_record()
    report = reconcile.run_reconciliation(
        database_alias="postgres",
        routes=test_backend.routes,
        policy_catalog=test_backend.policies,
        transport=fake_transport,
    )
    assert report.dispatched == 1
    other.refresh_from_db()
    own.refresh_from_db()
    assert other.dispatches_without_claim == 0 and own.dispatches_without_claim == 1
    assert (
        dispatch.publish_task(
            str(other.id),
            database_alias="postgres",
            routes=test_backend.routes,
            transport=fake_transport,
        )
        == dispatch.DispatchResult.NOT_ELIGIBLE
    )


def test_nondefault_backend_uses_its_own_manifest(test_backend, fake_transport):
    record = make_task_record(backend_alias="other")
    routes = {
        name: replace(route, source_queue_url=route.source_queue_url + "-other")
        for name, route in test_backend.routes.items()
    }
    result = reconcile.run_reconciliation(
        backend_alias="other",
        database_alias="postgres",
        routes=routes,
        policy_catalog=test_backend.policies,
        transport=fake_transport,
    )
    record.refresh_from_db()
    assert result.dispatched == 1 and record.dispatches_without_claim == 1
    assert fake_transport.queues == [routes["short"].source_queue_url]


def test_expired_corrupt_claim_is_reported_without_repair(test_backend, fake_transport):
    record = make_task_record(
        status="RUNNING",
        attempts_started=1,
        lease_token=uuid4(),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    report = reconcile.run_reconciliation(
        database_alias="postgres",
        routes=test_backend.routes,
        policy_catalog=test_backend.policies,
        transport=fake_transport,
    )
    record.refresh_from_db()
    assert report.invariant_violations == 1 and report.recovered == 0
    assert record.status == "RUNNING" and not fake_transport.sent
    assert invariants.find_violations("postgres")[0]["task_id"] == str(record.id)


def test_finalization_refuses_missing_attempt_and_rolls_back():
    token = uuid4()
    record = make_task_record(
        status="RUNNING",
        attempts_started=1,
        lease_token=token,
        lease_expires_at=timezone.now() + timedelta(seconds=90),
    )
    with pytest.raises(InvariantViolation):
        outcomes.finalize_success(
            task_id=str(record.id), attempt_id=token, result=3, database_alias="postgres"
        )
    record.refresh_from_db()
    assert record.status == "RUNNING" and record.result is None


def test_terminal_hold_is_rejected_by_postgres():
    with pytest.raises(IntegrityError), transaction.atomic(using="postgres"):
        make_task_record(
            status="SUCCESSFUL",
            finished_at=timezone.now(),
            held_at=timezone.now(),
            hold_reason="ambiguous_external_outcome",
        )


def test_foreign_claim_cannot_execute_on_default_backend():
    record = make_task_record(backend_alias="other")
    assert (
        claims.claim_task(
            task_id=str(record.id),
            queue_name="short",
            worker_name="short",
            lease_seconds=90,
            database_alias="postgres",
        )
        is None
    )


def test_due_shortcut_uses_database_clock(test_backend, monkeypatch):
    record = make_task_record(available_at=timezone.now() - timedelta(minutes=1))
    monkeypatch.setattr(timezone, "now", lambda: record.available_at - timedelta(hours=1))
    handle_task(
        str(record.id),
        backend=test_backend,
        worker=WorkerIdentity("short", ""),
        remaining_seconds=60,
    )
    record.refresh_from_db()
    assert record.status == "SUCCESSFUL"


def test_invariant_review_audits_without_repairing_history(test_backend):
    from django_tasks_aws.models import TaskControlEvent
    from django_tasks_aws.services.control import ControlRequest, operate

    record = make_task_record(
        status="RUNNING",
        attempts_started=1,
        lease_token=uuid4(),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    request = ControlRequest("corruption-review", "Incident evidence retained", "operator")
    result = operate(
        backend=test_backend, task_id=str(record.id), action="invariant_review", request=request
    )
    repeat = operate(
        backend=test_backend, task_id=str(record.id), action="invariant_review", request=request
    )
    record.refresh_from_db()
    assert not result.repeated and repeat.repeated
    assert record.status == "RUNNING" and record.attempts.count() == 0
    assert TaskControlEvent.objects.using("postgres").filter(task_id=record.id).count() == 1
