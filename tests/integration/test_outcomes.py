"""Ownership-checked completion and retry transitions."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from django_tasks_aws.models import AttemptStatus, HoldReason, TaskRecord, TaskStatus
from django_tasks_aws.services import claims, outcomes
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def claim(record):
    return claims.claim_task(
        task_id=record.id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )


def test_finalize_success():
    record = make_task_record()
    claimed = claim(record)

    ok = outcomes.finalize_success(
        task_id=record.id, attempt_id=claimed.attempt_id, result=3, database_alias="postgres"
    )

    assert ok is True
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL
    assert record.result == 3
    assert record.lease_token is None
    assert record.finished_at is not None
    assert record.attempts.filter(id=claimed.attempt_id).get().status == AttemptStatus.SUCCESSFUL


def test_finalize_with_wrong_token_is_noop():
    record = make_task_record()
    claim(record)

    ok = outcomes.finalize_success(
        task_id=record.id, attempt_id=uuid.uuid4(), result=3, database_alias="postgres"
    )

    assert ok is False
    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING


def test_finalize_after_lease_expiry_is_noop():
    record = make_task_record()
    claimed = claim(record)
    TaskRecord.objects.using("postgres").filter(pk=record.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )

    ok = outcomes.finalize_success(
        task_id=record.id, attempt_id=claimed.attempt_id, result=3, database_alias="postgres"
    )

    assert ok is False
    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING


def test_finalize_retry_schedules_due_time():
    record = make_task_record()
    claimed = claim(record)
    retry_at = timezone.now() + timedelta(minutes=1)

    ok = outcomes.finalize_retry(
        task_id=record.id,
        attempt_id=claimed.attempt_id,
        retry_at=retry_at,
        database_alias="postgres",
    )

    assert ok is True
    record.refresh_from_db()
    assert record.status == TaskStatus.PENDING
    assert record.available_at == retry_at
    assert record.lease_token is None


def test_finalize_failure_records_error():
    record = make_task_record()
    claimed = claim(record)

    ok = outcomes.finalize_failure(
        task_id=record.id,
        attempt_id=claimed.attempt_id,
        error={"error_type": "X.Y", "message": "boom", "uncertain": False},
        database_alias="postgres",
    )

    assert ok is True
    record.refresh_from_db()
    assert record.status == TaskStatus.FAILED
    assert record.last_error["error_type"] == "X.Y"
    assert record.finished_at is not None


def test_finalize_hold_keeps_pending_with_hold():
    record = make_task_record()
    claimed = claim(record)

    ok = outcomes.finalize_hold(
        task_id=record.id,
        attempt_id=claimed.attempt_id,
        hold_reason=HoldReason.AMBIGUOUS_OUTCOME,
        database_alias="postgres",
    )

    assert ok is True
    record.refresh_from_db()
    assert record.status == TaskStatus.PENDING
    assert record.held_at is not None
    assert record.hold_reason == HoldReason.AMBIGUOUS_OUTCOME
