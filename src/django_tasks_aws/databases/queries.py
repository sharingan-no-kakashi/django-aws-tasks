"""Shared ORM predicates for database-clock eligibility."""

from django.db.models import F, Q, QuerySet

from django_tasks_aws.databases import DatabaseClock
from django_tasks_aws.models import TaskRecord, TaskStatus

# Preserve imports used by existing service callers.
ClockTimestamp = DatabaseClock


def get_due_pending_tasks(database_alias: str, backend_alias: str) -> QuerySet[TaskRecord]:
    return TaskRecord.objects.using(database_alias).filter(
        backend_alias=backend_alias,
        status=TaskStatus.PENDING,
        held_at__isnull=True,
        available_at__lte=DatabaseClock(),
        next_dispatch_at__lte=DatabaseClock(),
        attempts_started__lt=F("max_attempts"),
    )


def build_reservation_filter() -> Q:
    return Q(dispatch_token__isnull=True) | Q(dispatch_reserved_until__lte=DatabaseClock())
