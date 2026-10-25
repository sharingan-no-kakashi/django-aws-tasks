"""Bounded advisory progress fenced by the current attempt and unexpired lease."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings

from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import lock_one
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.models import AttemptStatus, TaskAttempt, TaskRecord, TaskStatus
from django_tasks_aws.services.inspection import get_database_time


@dataclass(frozen=True)
class ProgressOwner:
    task_id: str
    attempt_id: UUID
    database_alias: str


@dataclass(frozen=True)
class Progress:
    stage: str
    completed: int | None = None
    total: int | None = None
    unit: str = ""
    message: str = ""

    def __post_init__(self):
        for label, value, maximum in (
            ("stage", self.stage, 80),
            ("unit", self.unit, 32),
            ("message", self.message, 500),
        ):
            if not isinstance(value, str) or len(value) > maximum:
                raise ValueError(
                    f"Progress {label} must be a string of at most {maximum} characters"
                )
        if not self.stage.strip():
            raise ValueError("Progress stage must not be empty")
        for count in (self.completed, self.total):
            if count is not None and (type(count) is not int or not 0 <= count <= 2**53 - 1):
                raise ValueError("Progress counts must be bounded nonnegative integers")
        if self.total is not None and (self.completed is None or self.completed > self.total):
            raise ValueError("Progress completed must be supplied and must not exceed total")


_owner: ContextVar[ProgressOwner | None] = ContextVar("task_progress_owner", default=None)


@contextmanager
def manage_progress_scope(task_id, attempt_id, database_alias):
    token = _owner.set(ProgressOwner(str(task_id), attempt_id, database_alias))
    try:
        yield
    finally:
        _owner.reset(token)


def report_progress(*, stage, completed=None, total=None, unit="", message="") -> bool:
    """Return false when throttled or stale; never complete work or renew its lease."""
    progress = Progress(stage, completed, total, unit, message)
    owner = _owner.get()
    if owner is None:
        raise ConfigurationError("Progress can only be reported inside a claimed task execution")
    return write_progress(owner, progress, get_minimum_interval())


def get_minimum_interval():
    seconds = getattr(settings, "TASKS_AWS_PROGRESS_MIN_INTERVAL_SECONDS", 1.0)
    if type(seconds) not in (int, float) or not 0.1 <= seconds <= 60:
        raise ConfigurationError("Progress minimum interval must be between 0.1 and 60 seconds")
    return timedelta(seconds=seconds)


@scoped(manage_coordination_transaction, "owner.database_alias")
def write_progress(owner, progress, interval):
    alias = owner.database_alias
    record = lock_one(TaskRecord.objects.using(alias).filter(pk=owner.task_id))
    now = get_database_time(alias)
    if not owns_live_lease(record, owner, now):
        return False
    attempt = lock_one(
        TaskAttempt.objects.using(alias).filter(
            pk=owner.attempt_id, task_id=owner.task_id, status=AttemptStatus.RUNNING
        )
    )
    if attempt is None or (attempt.progress_at and now - attempt.progress_at < interval):
        return False
    TaskAttempt.objects.using(alias).filter(pk=attempt.pk).update(
        progress=asdict(progress), progress_at=now
    )
    return True


def owns_live_lease(record, owner, now):
    return (
        record is not None
        and record.status == TaskStatus.RUNNING
        and record.lease_token == owner.attempt_id
        and record.lease_expires_at is not None
        and record.lease_expires_at > now
    )
