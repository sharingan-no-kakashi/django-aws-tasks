"""Ownership-checked completion and retry transitions.

Lock the task before checking database-clock expiry, then lock its attempt.
A finalizer that waits for a row lock must not act on a lease that expired
while it waited.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from django_tasks_aws import telemetry
from django_tasks_aws.databases import DatabaseClock
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import lock_one
from django_tasks_aws.databases.queries import build_reservation_filter, get_due_pending_tasks
from django_tasks_aws.decorators import scoped
from django_tasks_aws.models import (
    AttemptOutcome,
    AttemptStatus,
    HoldReason,
    TaskAttempt,
    TaskRecord,
    TaskStatus,
)
from django_tasks_aws.services.inspection import get_database_time
from django_tasks_aws.services.invariants import require_current_attempt


class Finalization(StrEnum):
    SUCCESS = "Successful"
    FAILURE = "Failed"
    RETRY = "Retried"
    HOLD = "Held"


def finalize_success(
    *,
    task_id: str,
    attempt_id: uuid.UUID,
    result,
    database_alias: str,
    deadline: float | None = None,
) -> bool:
    return finalize(
        database_alias=database_alias,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome=Finalization.SUCCESS,
        changes={
            "status": TaskStatus.SUCCESSFUL,
            "result": result,
            "finished_at": DatabaseClock(),
        },
        deadline=deadline,
    )


def finalize_retry(
    *,
    task_id: str,
    attempt_id: uuid.UUID,
    retry_at: datetime,
    database_alias: str,
    error: dict | None = None,
    deadline: float | None = None,
) -> bool:
    return finalize(
        database_alias=database_alias,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome=Finalization.RETRY,
        changes={
            "status": TaskStatus.PENDING,
            "available_at": retry_at,
            "next_dispatch_at": retry_at,
        },
        error=error,
        deadline=deadline,
    )


def finalize_failure(
    *,
    task_id: str,
    attempt_id: uuid.UUID,
    error: dict,
    database_alias: str,
    deadline: float | None = None,
) -> bool:
    return finalize(
        database_alias=database_alias,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome=Finalization.FAILURE,
        changes={"status": TaskStatus.FAILED, "finished_at": DatabaseClock()},
        error=error,
        deadline=deadline,
    )


def finalize_hold(
    *,
    task_id: str,
    attempt_id: uuid.UUID,
    hold_reason: HoldReason,
    database_alias: str,
    error: dict | None = None,
    deadline: float | None = None,
) -> bool:
    return finalize(
        database_alias=database_alias,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome=Finalization.HOLD,
        changes={
            "status": TaskStatus.PENDING,
            "held_at": DatabaseClock(),
            "hold_reason": hold_reason,
        },
        error=error,
        deadline=deadline,
    )


def hold_pending(*, task_id: str, hold_reason: HoldReason, database_alias: str) -> bool:
    """Conditionally hold a still-pending, unheld task, clearing its reservation."""
    records = TaskRecord.objects.using(database_alias).filter(
        pk=task_id, status=TaskStatus.PENDING, held_at__isnull=True
    )
    return hold_pending_record(records, hold_reason, database_alias)


def hold_delivery_exhausted(
    *, task_id: str, dispatch_limit: int, database_alias: str, backend_alias: str = "default"
) -> bool:
    """Hold a pending task only if its unclaimed-dispatch budget is still exhausted."""
    records = get_due_pending_tasks(database_alias, backend_alias).filter(
        build_reservation_filter(), pk=task_id, dispatches_without_claim__gte=dispatch_limit
    )
    return hold_pending_record(records, HoldReason.DELIVERY_EXHAUSTED, database_alias)


def hold_pending_record(records, hold_reason, database_alias):
    record = hold_locked_record(records, hold_reason, database_alias)
    if record is None:
        return False
    report_task_finalization(
        str(record.pk), None, database_alias, queue=record.queue_name, outcome="Held"
    )
    return True


def finalize(
    *,
    database_alias: str,
    task_id: str,
    attempt_id: uuid.UUID,
    changes: dict,
    outcome: Finalization,
    error: dict | None = None,
    deadline: float | None = None,
) -> bool:
    error = telemetry.sanitize_error(error) if error is not None else None
    record = finalize_owned_attempt(database_alias, task_id, attempt_id, changes, outcome, error)
    if record is None:
        return False
    report_finalization(record, attempt_id, outcome, database_alias)
    notify_finalization(task_id, database_alias, deadline)
    return True


def notify_finalization(task_id, database_alias, deadline=None):
    from django.db import transaction

    from django_tasks_aws.workflows.adapter import uses_aws_workflows

    if not uses_aws_workflows():
        return
    from django_tasks_aws.workflows.aws import advance_after_task

    transaction.on_commit(
        lambda: advance_after_task(
            sender=TaskRecord,
            task_id=task_id,
            database_alias=database_alias,
            deadline=deadline,
        ),
        using=database_alias,
        robust=True,
    )


def finish_task(record, changes, error, database_alias):
    if error is not None:
        changes = {**changes, "last_error": error}
    TaskRecord.objects.using(database_alias).filter(pk=record.pk).update(
        lease_token=None,
        lease_expires_at=None,
        updated_at=DatabaseClock(),
        **changes,
    )


def finish_attempt(task_id, attempt_id, outcome, error, database_alias):
    status = AttemptStatus.FAILED
    attempt_outcome = AttemptOutcome.CONFIRMED_FAILURE
    match outcome:
        case Finalization.SUCCESS:
            status = AttemptStatus.SUCCESSFUL
            attempt_outcome = AttemptOutcome.CONFIRMED_SUCCESS
        case Finalization.HOLD:
            attempt_outcome = AttemptOutcome.UNKNOWN
        case Finalization.FAILURE | Finalization.RETRY:
            pass
        case _:
            raise ValueError(f"Unsupported finalization: {outcome}")
    error = error or {}
    TaskAttempt.objects.using(database_alias).filter(pk=attempt_id, task_id=task_id).update(
        status=status,
        outcome=attempt_outcome,
        finished_at=DatabaseClock(),
        error_type=error.get("error_type"),
        error_message=error.get("message"),
    )


def get_owned_task(database_alias: str, task_id: str, attempt_id: uuid.UUID) -> TaskRecord | None:
    records = TaskRecord.objects.using(database_alias).filter(pk=task_id, status=TaskStatus.RUNNING)
    record = lock_one(records)
    if record is None:
        return None
    if (
        record.lease_token != attempt_id
        or record.lease_expires_at is None
        or record.lease_expires_at <= get_database_time(database_alias)
    ):
        telemetry.log_event(
            "attempt.stale", task_id=task_id, attempt_id=attempt_id, task_name=record.task_name
        )
        return None
    require_current_attempt(database_alias, task_id, attempt_id)
    return record


def report_finalization(record, attempt_id, outcome, database_alias):
    report_task_finalization(
        str(record.pk),
        attempt_id,
        database_alias,
        outcome=outcome.value,
        queue=record.queue_name,
        task_name=record.task_name,
        backend=record.backend_alias,
        attempt_number=record.attempts_started,
        producer_version=record.producer_version,
    )


__all__ = [
    "finalize_success",
    "finalize_retry",
    "finalize_failure",
    "finalize_hold",
    "hold_pending",
    "hold_delivery_exhausted",
]


@scoped(manage_coordination_transaction, "database_alias")
def hold_locked_record(records, hold_reason, database_alias):
    record = lock_one(records)
    if record is None:
        return None
    records.filter(pk=record.pk).update(
        held_at=DatabaseClock(),
        hold_reason=hold_reason,
        dispatch_token=None,
        dispatch_reserved_until=None,
        updated_at=DatabaseClock(),
    )
    return record


@scoped(manage_coordination_transaction, "database_alias")
def finalize_owned_attempt(database_alias, task_id, attempt_id, changes, outcome, error):
    record = get_owned_task(database_alias, task_id, attempt_id)
    if record is None:
        return None
    finish_task(record, changes, error, database_alias)
    finish_attempt(task_id, attempt_id, outcome, error, database_alias)
    return record


def report_task_finalization(task_id, attempt_id, database_alias, *, queue, outcome, **fields):
    from django_tasks_aws.workflows.adapter import get_workflow_fields

    telemetry.report_after_commit(
        database_alias,
        "attempt.finalized" if attempt_id is not None else "task.held",
        metric_name=outcome,
        queue=queue,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome=outcome,
        **get_workflow_fields(task_id, database_alias),
        **fields,
    )
