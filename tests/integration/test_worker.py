"""SQS worker handler: envelope parsing, classification, and response."""

import json
import uuid

import pytest
from django.utils import timezone

from django_tasks_aws.models import HoldReason, TaskStatus
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services.claims import AttemptMetadata
from django_tasks_aws.services.worker import MessageDecision, handle_task, process_record
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]

WORKER = WorkerIdentity(name="short", source_arn="arn:aws:sqs:us-east-1:123:short")


def handle(task_id, test_backend, remaining=60.0):
    return handle_task(
        task_id,
        backend=test_backend,
        worker=WORKER,
        remaining_seconds=remaining,
        metadata=AttemptMetadata(sqs_message_id="m1"),
    )


def test_terminal_task_acknowledges_without_executing(test_backend):
    record = make_task_record(status=TaskStatus.SUCCESSFUL, finished_at=timezone.now())
    assert handle(record.id, test_backend) is MessageDecision.ACK


def test_held_task_acknowledges(test_backend):
    record = make_task_record(held_at=timezone.now(), hold_reason="ambiguous_external_outcome")
    assert handle(record.id, test_backend) is MessageDecision.ACK


def test_not_due_task_acknowledges(test_backend):
    from datetime import timedelta

    record = make_task_record(available_at=timezone.now() + timedelta(hours=1))
    assert handle(record.id, test_backend) is MessageDecision.ACK


def test_missing_task_is_delivery_failure(test_backend):
    assert handle(str(uuid.uuid4()), test_backend) is MessageDecision.DELIVERY_FAILURE


def test_due_task_is_claimed_and_executed(test_backend):
    record = make_task_record(task_name="tests.demo_tasks.add")

    assert handle(record.id, test_backend) is MessageDecision.ACK
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL
    assert record.result == 3


def test_unsupported_version_is_held(test_backend):
    record = make_task_record(payload_version=2)
    assert handle(record.id, test_backend) is MessageDecision.ACK
    record.refresh_from_db()
    assert record.held_at is not None
    assert record.hold_reason == HoldReason.INCOMPATIBLE_PAYLOAD


def test_process_record_rejects_wrong_source(test_backend):
    record = {"messageId": "m1", "eventSourceARN": "arn:aws:sqs:us-east-1:123:other", "body": "{}"}
    decision = process_record(
        record, backend=test_backend, worker=WORKER, remaining_seconds=60.0, context=None
    )
    assert decision is MessageDecision.DELIVERY_FAILURE


def test_process_record_rejects_malformed_body(test_backend):
    record = {"messageId": "m1", "eventSourceARN": WORKER.source_arn, "body": "not-json"}
    decision = process_record(
        record, backend=test_backend, worker=WORKER, remaining_seconds=60.0, context=None
    )
    assert decision is MessageDecision.DELIVERY_FAILURE


def test_process_record_accepts_valid_envelope(test_backend):
    task = make_task_record(task_name="tests.demo_tasks.add")
    body = json.dumps({"schema_version": 1, "task_id": str(task.id)})
    record = {"messageId": "m1", "eventSourceARN": WORKER.source_arn, "body": body}

    decision = process_record(
        record, backend=test_backend, worker=WORKER, remaining_seconds=60.0, context=None
    )
    assert decision is MessageDecision.ACK
    task.refresh_from_db()
    assert task.status == TaskStatus.SUCCESSFUL


def test_wrong_route_delivery_is_rejected(test_backend):
    task = make_task_record(queue_name="documents", task_name="tests.demo_tasks.add")

    assert handle(task.id, test_backend) is MessageDecision.DELIVERY_FAILURE
    task.refresh_from_db()
    assert task.status == TaskStatus.PENDING
