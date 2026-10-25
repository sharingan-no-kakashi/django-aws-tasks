"""Injected boundary faults assert durable ownership and transport decisions separately."""

import json
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import DatabaseError, connections, transaction
from django.utils import timezone

from django_tasks_aws.databases.context import open_coordination_cursor
from django_tasks_aws.models import HoldReason, TaskAttempt, TaskStatus
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services import claims, dispatch, outcomes, worker
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]
WORKER = WorkerIdentity("short", "arn:aws:sqs:us-east-1:123:short")


def claim(record):
    return claims.claim_task(
        task_id=str(record.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )


def handle(record, backend):
    return worker.handle_task(str(record.id), backend=backend, worker=WORKER, remaining_seconds=60)


def test_reservation_survives_publisher_death_and_consumes_budget(manifest, fake_transport):
    record = make_task_record()
    with open_coordination_cursor("postgres") as cursor:
        assert dispatch.reserve(
            cursor,
            token=uuid4(),
            task_id=record.id,
            queue_name="short",
            reservation_seconds=15,
            dispatch_limit=12,
        )
    assert (
        dispatch.publish_task(
            str(record.id), database_alias="postgres", routes=manifest, transport=fake_transport
        )
        == dispatch.DispatchResult.NOT_ELIGIBLE
    )
    record.refresh_from_db()
    assert record.dispatches_without_claim == 1 and record.attempts_started == 0
    assert not fake_transport.sent


def test_accepted_send_with_failed_metadata_commit_remains_recoverable(
    monkeypatch, manifest, fake_transport
):
    record = make_task_record()
    monkeypatch.setattr(dispatch, "confirm", Mock(side_effect=DatabaseError("commit unavailable")))
    with pytest.raises(DatabaseError):
        dispatch.publish_task(
            str(record.id), database_alias="postgres", routes=manifest, transport=fake_transport
        )
    record.refresh_from_db()
    assert len(fake_transport.sent) == 1 and record.dispatches_without_claim == 1
    assert record.dispatch_token and record.last_dispatched_at is None
    assert record.status == TaskStatus.PENDING


def test_ambiguous_claim_commit_never_runs_handler(monkeypatch, test_backend):
    record = make_task_record()
    real_claim = claims.claim_task

    def committed_then_disconnected(**options):
        real_claim(**options)
        raise DatabaseError("lost claim commit response")

    execute = Mock()
    monkeypatch.setattr(claims, "claim_task", committed_then_disconnected)
    monkeypatch.setattr(worker.execution, "execute_claimed_task", execute)
    message = {
        "messageId": "claim",
        "eventSourceARN": WORKER.source_arn,
        "body": json.dumps({"schema_version": 1, "task_id": str(record.id)}),
    }
    assert (
        worker.process_record(
            message, backend=test_backend, worker=WORKER, remaining_seconds=60, context=None
        )
        == worker.MessageDecision.DELIVERY_FAILURE
    )
    execute.assert_not_called()
    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING and record.attempts_started == 1
    assert TaskAttempt.objects.using("postgres").filter(task=record).count() == 1


def test_duplicate_live_and_expired_leases_ack_without_extra_attempt(test_backend):
    record = make_task_record()
    claim(record)
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    type(record).objects.using("postgres").filter(pk=record.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING and record.attempts_started == 1


def test_success_then_lost_ack_does_not_repeat_effect(test_backend):
    record = make_task_record()
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    record.refresh_from_db()
    assert record.result == 3 and record.attempts_started == 1


def test_retry_then_lost_ack_cannot_claim_before_due(test_backend):
    record = make_task_record()
    owned = claim(record)
    due = timezone.now() + timedelta(hours=1)
    assert outcomes.finalize_retry(
        task_id=str(record.id),
        attempt_id=owned.attempt_id,
        retry_at=due,
        error={},
        database_alias="postgres",
    )
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    record.refresh_from_db()
    assert record.attempts_started == 1 and record.available_at == due


def test_held_inflight_publication_cannot_confirm(monkeypatch, manifest):
    record = make_task_record()

    def send(queue, body):
        assert not connections["postgres"].in_atomic_block
        assert outcomes.hold_pending(
            task_id=str(record.id),
            hold_reason=HoldReason.INCOMPATIBLE_PAYLOAD,
            database_alias="postgres",
        )

    dispatch.publish_task(
        str(record.id),
        database_alias="postgres",
        routes=manifest,
        transport=Mock(send_message=send),
    )
    record.refresh_from_db()
    assert record.held_at and record.last_dispatched_at is None
    assert record.dispatches_without_claim == 1


def test_partial_batch_reports_sqs_message_ids(monkeypatch, test_backend):
    record = make_task_record()
    import django.tasks

    monkeypatch.setattr(django.tasks, "default_task_backend", test_backend)
    monkeypatch.setenv("TASK_WORKER_NAME", "short")
    monkeypatch.setenv("TASK_WORKER_SOURCE_ARN", test_backend.routes["short"].source_queue_arn)
    records = [
        {
            "messageId": name,
            "eventSourceARN": test_backend.routes["short"].source_queue_arn,
            "body": body,
        }
        for name, body in [
            ("ok", json.dumps({"schema_version": 1, "task_id": str(record.id)})),
            ("bad", "not-json"),
        ]
    ]
    response = worker.process_event({"Records": records}, None)
    assert response == {"batchItemFailures": [{"itemIdentifier": "bad"}]}
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL


def test_outer_rollback_emits_no_false_completion_metric(test_backend, caplog):
    record = make_task_record()
    owned = claim(record)
    with (
        caplog.at_level("INFO", logger="django_tasks_aws.events"),
        transaction.atomic(using="postgres"),
    ):
        assert outcomes.finalize_success(
            task_id=str(record.id),
            attempt_id=owned.attempt_id,
            result=3,
            database_alias="postgres",
        )
        transaction.set_rollback(True, using="postgres")
    assert not any('"Successful"' in entry.message for entry in caplog.records)
    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING


def test_losing_claim_acknowledges_without_executing(monkeypatch, test_backend):
    record = make_task_record()
    execute = Mock()
    monkeypatch.setattr(claims, "claim_task", lambda **options: None)
    monkeypatch.setattr(worker.execution, "execute_claimed_task", execute)
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    execute.assert_not_called()
    record.refresh_from_db()
    assert record.attempts_started == 0 and record.status == TaskStatus.PENDING


@pytest.mark.parametrize(
    "task_path, retryable, expected_status",
    [
        ("tests.demo_tasks.fail_transiently", True, TaskStatus.PENDING),
        ("tests.demo_tasks.fail_permanently", False, TaskStatus.FAILED),
    ],
)
def test_committed_business_failure_acknowledges(
    test_backend, task_path, retryable, expected_status
):
    from django_tasks_aws import policies

    test_backend.policies = policies.build_policy_catalog(
        max_attempts=5,
        allowed_routes=("short",),
        overrides={
            task_path: {"retryable_exception_paths": ["builtins.RuntimeError"] if retryable else []}
        },
    )
    record = make_task_record(task_name=task_path, payload={"args": ["x"], "kwargs": {}})
    assert handle(record, test_backend) == worker.MessageDecision.ACK
    record.refresh_from_db()
    assert record.status == expected_status and record.attempts_started == 1
    assert record.attempts.get().outcome == "CONFIRMED_FAILURE"


def test_repeated_initialization_failures_exhaust_delivery_not_execution(
    test_backend, monkeypatch, fake_transport
):
    import django.tasks

    from django_tasks_aws.exceptions import ConfigurationError
    from django_tasks_aws.services import reconcile

    record = make_task_record()
    monkeypatch.setattr(django.tasks, "default_task_backend", test_backend)
    monkeypatch.setenv("TASK_WORKER_NAME", "invalid-profile")
    for _ in range(3):
        with pytest.raises(ConfigurationError):
            worker.process_event({"Records": []}, None)
    record.refresh_from_db()
    assert record.attempts_started == 0 and record.attempts.count() == 0
    type(record).objects.using("postgres").filter(pk=record.id).update(dispatches_without_claim=12)
    assert (
        reconcile.hold_delivery_exhausted(
            database_alias="postgres", routes=test_backend.routes, per_queue_limit=10
        )
        == 1
    )
    record.refresh_from_db()
    assert record.hold_reason == HoldReason.DELIVERY_EXHAUSTED and record.attempts_started == 0
