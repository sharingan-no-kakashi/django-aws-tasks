"""Atomic execution claims and fixed leases.

A short transaction locks the task, checks eligibility, allocates its lease,
and creates the attempt. Business execution begins only after commit.
"""

import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta

from django.apps import apps
from django.db.models import F
from django.db.models.functions import Coalesce

from django_tasks_aws import telemetry
from django_tasks_aws.databases import DatabaseClock
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import lock_one
from django_tasks_aws.decorators import scoped
from django_tasks_aws.models import (
    AttemptOutcome,
    AttemptStatus,
    TaskAttempt,
    TaskRecord,
    TaskStatus,
)
from django_tasks_aws.workflows.adapter import get_workflow_fields


@dataclass(frozen=True)
class AttemptMetadata:
    """Invocation/log identity recorded on the attempt at claim time."""

    lambda_request_id: str = ""
    log_group: str = ""
    log_stream: str = ""
    aws_region: str = ""
    worker_version: str = ""
    function_version: str = ""
    sqs_message_id: str = ""


@dataclass(frozen=True)
class ClaimedTask:
    task_id: str
    attempt_id: uuid.UUID
    attempt_number: int
    task_name: str
    worker_name: str


def claim_task(
    *,
    task_id: str,
    queue_name: str,
    worker_name: str,
    lease_seconds: int,
    database_alias: str,
    metadata: AttemptMetadata | None = None,
    backend_alias: str = "default",
) -> ClaimedTask | None:
    """Atomically claim due, unheld pending work for this worker identity."""
    metadata = metadata or AttemptMetadata()
    attempt_id = uuid.uuid4()

    record = claim_record(
        task_id,
        queue_name,
        worker_name,
        lease_seconds,
        database_alias,
        backend_alias,
        attempt_id,
        metadata,
    )
    if record is None:
        return None

    report_claim(record, attempt_id, worker_name, metadata, database_alias)
    return ClaimedTask(
        task_id=str(record.pk),
        attempt_id=attempt_id,
        attempt_number=record.attempts_started,
        task_name=record.task_name,
        worker_name=worker_name,
    )


def is_workflow_claim_allowed(task_id, alias):
    if not apps.is_installed("django_tasks_aws.workflows"):
        return True
    from django_tasks_aws.workflows.models import WorkflowNode, WorkflowRun

    run_id = (
        WorkflowNode.objects.using(alias)
        .filter(task_id=task_id)
        .values_list("run_id", flat=True)
        .first()
    )
    if run_id is None:
        return True
    run = WorkflowRun.objects.using(alias).select_for_update().get(pk=run_id)
    return run.cancel_requested_at is None


def report_claim(record, attempt_id, worker_name, metadata, database_alias):
    telemetry.report_after_commit(
        database_alias,
        "attempt.claimed",
        metric_name="AttemptsStarted",
        task_id=str(record.pk),
        attempt_id=attempt_id,
        queue=record.queue_name,
        worker=worker_name,
        task_name=record.task_name,
        attempt_number=record.attempts_started,
        backend=record.backend_alias,
        worker_version=metadata.worker_version,
        lambda_request_id=metadata.lambda_request_id,
        **get_workflow_fields(record.pk, database_alias),
    )


def lock_claimable_task(database_alias, task_id, queue_name, backend_alias):
    records = TaskRecord.objects.using(database_alias).filter(
        pk=task_id,
        queue_name=queue_name,
        backend_alias=backend_alias,
        status=TaskStatus.PENDING,
        held_at__isnull=True,
        available_at__lte=DatabaseClock(),
        attempts_started__lt=F("max_attempts"),
    )
    return lock_one(records)


def assign_lease(record, attempt_id, lease_seconds, database_alias):
    TaskRecord.objects.using(database_alias).filter(pk=record.pk).update(
        status=TaskStatus.RUNNING,
        attempts_started=F("attempts_started") + 1,
        lease_token=attempt_id,
        lease_expires_at=DatabaseClock() + timedelta(seconds=lease_seconds),
        first_started_at=Coalesce("first_started_at", DatabaseClock()),
        last_attempted_at=DatabaseClock(),
        dispatch_token=None,
        dispatch_reserved_until=None,
        dispatches_without_claim=0,
        updated_at=DatabaseClock(),
    )
    record.refresh_from_db(using=database_alias)


def create_attempt(record, attempt_id, worker_name, metadata, database_alias):
    TaskAttempt.objects.using(database_alias).create(
        id=attempt_id,
        task_id=record.pk,
        attempt_number=record.attempts_started,
        status=AttemptStatus.RUNNING,
        outcome=AttemptOutcome.UNKNOWN,
        started_at=record.last_attempted_at,
        lease_expires_at=record.lease_expires_at,
        worker_name=worker_name,
        **asdict(metadata),
    )


def lease_covers(*, lease_seconds: int, remaining_seconds: float, margin_seconds: float) -> bool:
    """Whether the fixed lease still covers the invocation lifetime plus margin."""
    return lease_seconds >= remaining_seconds + margin_seconds


def get_remaining_lifetime(remaining_millis: int) -> float:
    """Convert a Lambda remaining-time budget to seconds."""
    return max(0.0, remaining_millis / 1000.0)


__all__ = [
    "AttemptMetadata",
    "ClaimedTask",
    "claim_task",
    "lease_covers",
    "get_remaining_lifetime",
]


@scoped(manage_coordination_transaction, "database_alias")
def claim_record(
    task_id,
    queue_name,
    worker_name,
    lease_seconds,
    database_alias,
    backend_alias,
    attempt_id,
    metadata,
):
    if not is_workflow_claim_allowed(task_id, database_alias):
        return None
    record = lock_claimable_task(database_alias, task_id, queue_name, backend_alias)
    if record is None or record.attempts.filter(status=AttemptStatus.RUNNING).exists():
        return None
    assign_lease(record, attempt_id, lease_seconds, database_alias)
    create_attempt(record, attempt_id, worker_name, metadata, database_alias)
    return record
