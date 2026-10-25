import json
import logging
from dataclasses import replace

import pytest

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.models import TaskStatus
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services import claims, dispatch, outcomes, reconcile
from django_tasks_aws.services.worker import MessageDecision, handle_task
from tests.conftest import make_manifest
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_paused_route_cannot_publish_or_claim_but_healthy_route_progresses(
    test_backend, fake_transport
):
    test_backend.routes["short"] = replace(test_backend.routes["short"], paused=True)
    short = make_task_record(queue_name="short")
    document = make_task_record(queue_name="documents")
    assert (
        dispatch.publish_task(
            str(short.id),
            routes=test_backend.routes,
            database_alias="postgres",
            transport=fake_transport,
        )
        == dispatch.DispatchResult.NOT_ELIGIBLE
    )
    assert (
        handle_task(
            str(short.id),
            backend=test_backend,
            worker=WorkerIdentity("short", "arn"),
            remaining_seconds=60,
        )
        == MessageDecision.DELIVERY_FAILURE
    )
    assert reconcile.dispatch_due(
        database_alias="postgres",
        routes=test_backend.routes,
        transport=fake_transport,
        per_queue_limit=10,
    ) == (1, 0)
    short.refresh_from_db()
    document.refresh_from_db()
    assert short.attempts_started == 0 and short.dispatches_without_claim == 0
    assert document.last_dispatched_at is not None


def test_pause_does_not_steal_existing_lease(test_backend):
    record = make_task_record()
    claim = claims.claim_task(
        task_id=str(record.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    test_backend.routes["short"] = replace(test_backend.routes["short"], paused=True)
    assert outcomes.finalize_success(
        task_id=str(record.id), attempt_id=claim.attempt_id, result=3, database_alias="postgres"
    )
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL


def test_options_pause_is_applied_to_shared_manifest():
    backend = DatabaseSqsBackend(
        "default",
        {
            "QUEUES": ["short", "documents"],
            "OPTIONS": {
                "workers": make_manifest(),
                "database_alias": "postgres",
                "paused_routes": ["short"],
                "allowed_task_modules": ["tests.demo_tasks"],
            },
        },
    )
    assert backend.routes["short"].paused and not backend.routes["documents"].paused


def test_completed_idle_sweep_emits_heartbeat_and_zero_age(
    test_backend, fake_transport, caplog, capsys
):
    with caplog.at_level(logging.INFO):
        report = reconcile.run_reconciliation(
            database_alias="postgres",
            routes=test_backend.routes,
            policy_catalog=test_backend.policies,
            transport=fake_transport,
        )
    metrics = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    events = [
        row.msg
        for row in caplog.records
        if row.name == "django-tasks-aws" and isinstance(row.msg, dict)
    ]
    assert report.dispatched == 0
    assert any(row.get("Heartbeat") == [1.0] for row in metrics)
    assert any(row.get("OldestDueAge") == [0.0] for row in metrics)
    assert any(row.get("event") == "sweep.completed" for row in events)


def test_handled_failure_emits_business_failure_metric(test_backend, capsys):
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
        error={"error_type": "builtins.RuntimeError", "message": "secret-token"},
        database_alias="postgres",
    )
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(row.get("Failed") == [1.0] and row.get("Queue") == "short" for row in records)
    assert "secret-token" not in str(records)
