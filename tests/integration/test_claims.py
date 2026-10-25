"""Atomic execution claims and fixed leases."""

from datetime import timedelta

import pytest
from django.utils import timezone

from django_tasks_aws.models import TaskAttempt, TaskStatus
from django_tasks_aws.services import claims
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def claim(task_id, queue_name="short", **overrides):
    kwargs = {
        "task_id": task_id,
        "queue_name": queue_name,
        "worker_name": "short",
        "lease_seconds": 90,
        "database_alias": "postgres",
    }
    kwargs.update(overrides)
    return claims.claim_task(**kwargs)


def test_claim_succeeds_on_due_pending():
    record = make_task_record()
    claimed = claim(record.id)

    assert claimed is not None
    assert claimed.task_id == str(record.id)
    assert claimed.attempt_number == 1

    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING
    assert record.lease_token == claimed.attempt_id
    assert record.attempts_started == 1
    assert record.dispatch_token is None
    assert TaskAttempt.objects.using("postgres").filter(task=record).count() == 1


def test_claim_rejects_not_due():
    record = make_task_record(available_at=timezone.now() + timedelta(hours=1))
    assert claim(record.id) is None


def test_claim_rejects_held():
    record = make_task_record(held_at=timezone.now(), hold_reason="ambiguous_external_outcome")
    assert claim(record.id) is None


def test_claim_rejects_terminal():
    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    assert claim(record.id) is None


def test_claim_rejects_wrong_route():
    record = make_task_record(queue_name="short")
    assert claim(record.id, queue_name="documents") is None


def test_claim_does_not_increment_attempts_on_duplicate():
    record = make_task_record()
    first = claim(record.id)
    second = claim(record.id)

    assert first is not None
    assert second is None
    record.refresh_from_db()
    assert record.attempts_started == 1


def test_lease_covers_helper():
    assert claims.lease_covers(lease_seconds=90, remaining_seconds=60, margin_seconds=5)
    assert not claims.lease_covers(lease_seconds=90, remaining_seconds=88, margin_seconds=5)
