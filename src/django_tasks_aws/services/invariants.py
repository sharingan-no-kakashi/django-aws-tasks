"""Read-only corruption detection and task-first history validation."""

from django.db.models import Exists, F, OuterRef, Q

from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import lock_one
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import InvariantViolation
from django_tasks_aws.models import (
    AttemptOutcome,
    AttemptStatus,
    TaskAttempt,
    TaskRecord,
    TaskStatus,
)


def get_matching_attempts(database_alias):
    return TaskAttempt.objects.using(database_alias).filter(
        task_id=OuterRef("pk"),
        pk=OuterRef("lease_token"),
        attempt_number=OuterRef("attempts_started"),
        status=AttemptStatus.RUNNING,
        outcome=AttemptOutcome.UNKNOWN,
        finished_at__isnull=True,
        worker_name=OuterRef("queue_name"),
    )


def get_suspect_tasks(database_alias, backend_alias):
    running = TaskAttempt.objects.using(database_alias).filter(
        task_id=OuterRef("pk"), status=AttemptStatus.RUNNING
    )
    other_running = running.exclude(pk=OuterRef("lease_token"))
    return (
        TaskRecord.objects.using(database_alias)
        .filter(backend_alias=backend_alias)
        .alias(
            matching_attempt=Exists(get_matching_attempts(database_alias)),
            any_running=Exists(running),
            other_running=Exists(other_running),
        )
        .filter(
            Q(status=TaskStatus.RUNNING, matching_attempt=False)
            | (~Q(status=TaskStatus.RUNNING) & Q(any_running=True))
            | Q(other_running=True)
        )
    )


def find_violations(database_alias, *, backend_alias="default", limit=100, task_id=None):
    if not 1 <= limit <= 1000:
        raise ValueError("Invariant inspection limit must be 1–1000")
    return inspect_task_invariants(database_alias, backend_alias, limit, task_id)


def require_current_attempt(database_alias, task_id, attempt_id):
    # The caller already owns the task row lock. Always lock history second.
    attempts = TaskAttempt.objects.using(database_alias).filter(task_id=task_id)
    candidates = attempts.filter(
        pk=attempt_id,
        attempt_number=F("task__attempts_started"),
        status=AttemptStatus.RUNNING,
        outcome=AttemptOutcome.UNKNOWN,
        finished_at__isnull=True,
        worker_name=F("task__queue_name"),
    )
    current = lock_one(candidates, only_self=True)
    if current is None:
        raise InvariantViolation(f"Task {task_id} has no matching current running attempt")
    if attempts.filter(status=AttemptStatus.RUNNING).exclude(pk=attempt_id).exists():
        raise InvariantViolation(f"Task {task_id} has multiple running attempts")


@scoped(manage_coordination_transaction, "database_alias")
def inspect_task_invariants(database_alias, backend_alias, limit, task_id):
    records = get_suspect_tasks(database_alias, backend_alias)
    if task_id is not None:
        records = records.filter(pk=task_id)
    return [
        {
            "task_id": str(record.pk),
            "status": record.status,
            "attempt_id": str(record.lease_token) if record.lease_token else None,
            "error_type": "InvariantViolation",
        }
        for record in records.order_by("created_at", "pk")[:limit]
    ]
