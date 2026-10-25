"""Transactional task creation and submission deduplication."""

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any, Protocol

from django.db import IntegrityError, transaction
from django.db.models.functions import Now
from django.tasks import Task, TaskResult, task_backends

from django_tasks_aws import policies, telemetry
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import (
    ConfigurationError,
    ExistingTaskResultExpired,
    SubmissionConflict,
)
from django_tasks_aws.import_paths import resolve_task
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.schemas import RouteManifest
from django_tasks_aws.serialization import (
    CURRENT_PAYLOAD_VERSION,
    build_payload,
    check_argument_size,
    compute_payload_hash,
    dumps,
    validate_payload,
)

_publication_deferred = ContextVar("task_publication_deferred", default=False)


@contextmanager
def defer_publication(enabled=True):
    """Leave accepted work durable for the sweeper when a callback has a deadline."""
    token = _publication_deferred.set(enabled or _publication_deferred.get())
    try:
        yield
    finally:
        _publication_deferred.reset(token)


@dataclass(frozen=True)
class EnqueueOutcome:
    record: TaskRecord
    created: bool


class AcceptanceBackend(Protocol):
    """Backend configuration and Django validation needed for durable acceptance."""

    alias: str
    database_alias: str
    allowed_task_modules: tuple[str, ...]
    policies: policies.PolicyCatalog
    producer_version: str
    immediate_dispatch: bool
    routes: RouteManifest
    result_retention: timedelta

    def validate_task(self, task: Task) -> None: ...


def submit(
    task: Task, *, args=(), kwargs=None, key=None, scope=None, operation_key=None
) -> TaskResult:
    """Submit through the selected backend without consuming application keyword arguments."""
    from django_tasks_aws.backends import DatabaseSqsBackend

    if not isinstance(task, Task):
        raise TypeError("submit() requires a Django Task")
    if not isinstance(args, (list, tuple)) or (kwargs is not None and not isinstance(kwargs, dict)):
        raise TypeError("args must be a sequence and kwargs must be a mapping")
    backend = task_backends[task.backend]
    if not isinstance(backend, DatabaseSqsBackend):
        raise ConfigurationError("submit() requires DatabaseSqsBackend")
    from django_tasks_aws.results import get_task_result

    outcome = enqueue(
        backend,
        task=task,
        args=tuple(args),
        kwargs={} if kwargs is None else kwargs,
        submission_key=key,
        dedupe_scope=scope,
        operation_key=operation_key,
    )
    return get_task_result(task, outcome.record)


def validate_identifier(value: Any, label: str):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 255):
        raise ConfigurationError(f"{label} must be a nonempty string of at most 255 characters")


def compute_submission_digest(task, identity_hash, operation_key):
    scheduled = task.run_after.astimezone(UTC).isoformat() if task.run_after is not None else None
    identity = (
        identity_hash,
        task.queue_name,
        scheduled,
        task.takes_context,
        task.priority,
        operation_key,
    )
    return hashlib.sha256(dumps(identity).encode()).hexdigest()


def enqueue(
    backend: AcceptanceBackend,
    *,
    task: Task,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    submission_key: str | None = None,
    dedupe_scope: str | None = None,
    operation_key: str | None = None,
    replay_of_id: str | None = None,
) -> EnqueueOutcome:
    """Validate and accept work in the caller's transaction, dispatching after commit.

    Identical keyed submissions return the original record. Different work under
    the same key conflicts. Acceptance is durable only after the outermost commit.
    """
    validate_task(backend, task)
    validate_identifier(submission_key, "submission_key")
    validate_identifier(dedupe_scope, "dedupe_scope")
    record = prepare_record(
        backend,
        task,
        args,
        kwargs,
        submission_key=submission_key,
        dedupe_scope=dedupe_scope,
        operation_key=operation_key,
        replay_of_id=replay_of_id,
    )
    outcome = accept_record(backend, record)
    if outcome.created:
        report_acceptance(backend, record)
    return outcome


def validate_task(backend: AcceptanceBackend, task: Task) -> None:
    backend.validate_task(task)
    if not backend.policies.get(task.module_path).allows_route(task.queue_name):
        raise ValueError(f"task {task.module_path!r} is not permitted on queue {task.queue_name!r}")
    resolve_task(task.module_path, allowed_modules=backend.allowed_task_modules)


def prepare_record(
    backend: AcceptanceBackend,
    task: Task,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    submission_key: str | None,
    dedupe_scope: str | None,
    operation_key: str | None,
    replay_of_id: str | None,
) -> TaskRecord:
    policy = backend.policies.get(task.module_path)
    payload = prepare_payload(args, kwargs)
    operation_key = (
        policies.derive_operation_key(policy, args, kwargs)
        if operation_key is None
        else policies.validate_operation_key(operation_key)
    )
    # Use database time unless the producer supplied an absolute schedule.
    available_at = task.run_after if task.run_after is not None else Now()
    identity_hash = compute_payload_hash(task.module_path, CURRENT_PAYLOAD_VERSION, payload)
    return TaskRecord(
        task_name=task.module_path,
        backend_alias=backend.alias,
        payload_version=CURRENT_PAYLOAD_VERSION,
        payload=payload,
        payload_hash=identity_hash,
        submission_hash=compute_submission_digest(task, identity_hash, operation_key),
        task_options={"takes_context": task.takes_context},
        queue_name=task.queue_name,
        available_at=available_at,
        next_dispatch_at=available_at,
        max_attempts=policy.max_attempts,
        dedupe_scope=dedupe_scope or backend.alias,
        dedupe_key=submission_key,
        operation_key=operation_key,
        producer_version=backend.producer_version,
        replay_of_id=replay_of_id,
    )


def prepare_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict:
    payload = build_payload(args, kwargs)
    validate_payload(payload)
    check_argument_size(payload)
    return payload


def accept_record(backend: AcceptanceBackend, record: TaskRecord) -> EnqueueOutcome:
    try:
        insert_task_record(backend.database_alias, record)
    except IntegrityError:
        if record.dedupe_key is None:
            raise
        existing = resolve_conflict(backend, record)
        return EnqueueOutcome(record=existing, created=False)
    return EnqueueOutcome(record=record, created=True)


def resolve_conflict(backend: AcceptanceBackend, candidate: TaskRecord) -> TaskRecord:
    existing = TaskRecord.objects.using(backend.database_alias).get(
        dedupe_scope=candidate.dedupe_scope,
        dedupe_key=candidate.dedupe_key,
    )
    identity_fields = (
        "task_name",
        "payload_version",
        "payload_hash",
        "backend_alias",
        "queue_name",
        "operation_key",
        "submission_hash",
    )
    if any(getattr(existing, field) != getattr(candidate, field) for field in identity_fields):
        raise SubmissionConflict(
            f"submission key {candidate.dedupe_key!r} was reused with different work or routing"
        )
    from django_tasks_aws.services.inspection import get_database_time

    if existing.details_expired_at or (
        existing.finished_at
        and existing.finished_at
        < get_database_time(backend.database_alias) - backend.result_retention
    ):
        raise ExistingTaskResultExpired(str(existing.id))
    return existing


def report_acceptance(backend: AcceptanceBackend, record: TaskRecord) -> None:
    telemetry.report_after_commit(
        backend.database_alias,
        "task.accepted",
        task_id=record.id,
        task_name=record.task_name,
        queue=record.queue_name,
        backend=backend.alias,
        producer_version=backend.producer_version,
    )
    if backend.immediate_dispatch:
        register_task_dispatch(
            str(record.id), backend.database_alias, backend.routes, backend.alias
        )


def register_task_dispatch(
    task_id: str, database_alias: str, routes: RouteManifest | None, backend_alias: str = "default"
) -> None:
    if _publication_deferred.get():
        return
    from django_tasks_aws.services import dispatch

    transaction.on_commit(
        lambda: dispatch.publish_task(
            task_id, database_alias=database_alias, routes=routes, backend_alias=backend_alias
        ),
        using=database_alias,
        robust=True,
    )


@scoped(transaction.atomic, using="database_alias")
def insert_task_record(database_alias: str, record: TaskRecord) -> None:
    record.save(using=database_alias, force_insert=True)
