"""Schema constraints enforced on real PostgreSQL.

These prove the durable records reject invalid state combinations at the
database boundary, not merely in application code.
"""

import pytest
from django.db import IntegrityError
from django.utils import timezone

from django_tasks_aws.models import TaskAttempt, TaskRecord, TaskStatus

DB = "postgres"

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def make_record(**overrides):
    data = {
        "task_name": "tests.demo_tasks.add",
        "backend_alias": "default",
        "payload_version": 1,
        "payload": {"args": [1], "kwargs": {}},
        "payload_hash": "h",
        "queue_name": "short",
        "status": TaskStatus.PENDING,
        "available_at": timezone.now(),
        "next_dispatch_at": timezone.now(),
        "max_attempts": 5,
        "dedupe_scope": "default",
        "producer_version": "0.1.0",
    }
    data.update(overrides)
    return TaskRecord.objects.using(DB).create(**data)


def make_attempt(record, number=1):
    return TaskAttempt.objects.using(DB).create(
        task=record,
        attempt_number=number,
        status="RUNNING",
        started_at=timezone.now(),
        worker_name="short",
    )


def test_pending_record_is_created():
    assert make_record().status == TaskStatus.PENDING


def test_running_record_requires_lease_fields():
    with pytest.raises(IntegrityError):
        make_record(status=TaskStatus.RUNNING)


def test_terminal_record_requires_finished_at():
    with pytest.raises(IntegrityError):
        make_record(status=TaskStatus.SUCCESSFUL)


def test_attempts_cannot_exceed_budget():
    with pytest.raises(IntegrityError):
        make_record(attempts_started=6, max_attempts=5)


def test_duplicate_dedupe_key_fails():
    make_record(dedupe_key="k1")
    with pytest.raises(IntegrityError):
        make_record(dedupe_key="k1")


def test_duplicate_attempt_ordinal_fails():
    record = make_record()
    make_attempt(record, number=1)
    with pytest.raises(IntegrityError):
        make_attempt(record, number=1)
