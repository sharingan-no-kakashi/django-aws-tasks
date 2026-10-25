"""Token-owned SQS publication.

A dispatch reservation is separate from an execution lease: a publisher owns
permission to emit a delivery hint, a worker owns permission to execute. The
reservation token fences the conditional metadata update so an obsolete or
stale publisher cannot overwrite a newer schedule.
"""

import random
import uuid
from datetime import timedelta
from enum import StrEnum
from typing import cast

from django.db.models import F

from django_tasks_aws import telemetry
from django_tasks_aws.databases import DatabaseClock
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import lock_one
from django_tasks_aws.databases.queries import build_reservation_filter, get_due_pending_tasks
from django_tasks_aws.decorators import scoped
from django_tasks_aws.models import TaskRecord, TaskStatus
from django_tasks_aws.schemas import RouteManifest
from django_tasks_aws.serialization import dumps, serialize_envelope
from django_tasks_aws.sqs import BotoSqsTransport, SqsTransport

DEFAULT_RESERVATION_SECONDS = 15
DEFAULT_BACKOFF_SECONDS = 30
PUBLICATION_BACKOFF_JITTER = 0.5


def compute_publication_backoff(*, base_seconds: int = DEFAULT_BACKOFF_SECONDS) -> float:
    """Bounded backoff around the default, jittered to spread publisher retries."""
    return random.uniform(
        base_seconds * (1 - PUBLICATION_BACKOFF_JITTER),
        base_seconds * (1 + PUBLICATION_BACKOFF_JITTER),
    )


class DispatchResult(StrEnum):
    DISPATCHED = "dispatched"
    NOT_ELIGIBLE = "not-eligible"


_transport: SqsTransport | None = None


def publish_task(
    task_id: str,
    *,
    transport: SqsTransport | None = None,
    database_alias: str | None = None,
    routes: RouteManifest | None = None,
    reservation_seconds: int = DEFAULT_RESERVATION_SECONDS,
    backend_alias: str = "default",
) -> DispatchResult:
    """Reserve, publish, and conditionally confirm a delivery for ``task_id``."""
    alias, manifest, transport = resolve_publication_defaults(
        database_alias, routes, transport, backend_alias
    )
    record = TaskRecord.objects.using(alias).filter(pk=task_id, backend_alias=backend_alias).first()
    if record is None:
        return DispatchResult.NOT_ELIGIBLE
    route = manifest.get(record.queue_name)
    if route is None or route.paused:
        return DispatchResult.NOT_ELIGIBLE

    token = reserve_publication(record, route, alias, reservation_seconds)
    if token is None:
        return DispatchResult.NOT_ELIGIBLE
    send_publication(record, route, token, transport, alias)
    confirm_publication(record, route, token, alias)
    return DispatchResult.DISPATCHED


def reserve_publication(record, route, database_alias, reservation_seconds):
    token = uuid.uuid4()
    reserved = reserve(
        database_alias,
        token=token,
        task_id=record.pk,
        queue_name=record.queue_name,
        reservation_seconds=reservation_seconds,
        dispatch_limit=route.dispatch_limit,
        backend_alias=record.backend_alias,
    )
    return token if reserved else None


def send_publication(record, route, token, transport, database_alias):
    body = dumps(serialize_envelope(str(record.pk)))
    try:
        transport.send_message(route.source_queue_url, body)
    except Exception as error:
        report_publication_failure(record, token, error)
        record_failure(
            database_alias,
            token=token,
            task_id=record.pk,
            backoff_seconds=compute_publication_backoff(),
        )
        raise
    telemetry.emit_metric("DispatchAccepted", queue=route.name)


def report_publication_failure(record, token, error):
    telemetry.log_event(
        "publication.failed",
        task_id=str(record.pk),
        queue=record.queue_name,
        dispatch_token=token,
        task_name=record.task_name,
        backend=record.backend_alias,
        error_type=type(error).__name__,
    )
    telemetry.emit_metric("DispatchFailures", queue=record.queue_name)


def confirm_publication(record, route, token, database_alias):
    confirmed = confirm(
        database_alias,
        token=token,
        task_id=record.pk,
        redispatch_seconds=route.redispatch_seconds,
    )
    telemetry.log_event(
        "publication.accepted" if confirmed else "publication.obsolete",
        task_id=str(record.pk),
        queue=route.name,
        task_name=record.task_name,
        dispatch_token=token,
        backend=record.backend_alias,
    )


def resolve_publication_defaults(database_alias, routes, transport, backend_alias):
    if database_alias is None or routes is None:
        from django.tasks import task_backends

        from django_tasks_aws.backends import DatabaseSqsBackend

        backend = cast(DatabaseSqsBackend, task_backends[backend_alias])

        if database_alias is None:
            database_alias = backend.database_alias
        if routes is None:
            routes = backend.routes
    return (
        database_alias,
        routes,
        transport if transport is not None else get_default_transport(),
    )


def get_default_transport() -> SqsTransport:
    global _transport
    if _transport is None:
        _transport = BotoSqsTransport()
    return _transport


def get_publication_alias(database):
    # Accept existing cursor-based callers while all queries run through the ORM.
    return database if isinstance(database, str) else database.db.alias


def manage_publication_transaction(operation):
    return scoped(
        manage_coordination_transaction,
        lambda arguments: get_publication_alias(arguments["database"]),
    )(operation)


@manage_publication_transaction
def reserve(
    database,
    *,
    token: uuid.UUID,
    task_id: uuid.UUID,
    queue_name: str,
    reservation_seconds: int,
    dispatch_limit: int,
    backend_alias: str = "default",
) -> bool:
    alias = get_publication_alias(database)
    if not lock_publication_task(alias, task_id):
        return False
    records = get_due_pending_tasks(alias, backend_alias).filter(
        build_reservation_filter(),
        pk=task_id,
        queue_name=queue_name,
        dispatches_without_claim__lt=dispatch_limit,
    )
    reserved_until = DatabaseClock() + timedelta(seconds=reservation_seconds)
    return bool(
        records.update(
            dispatch_token=token,
            dispatch_reserved_until=reserved_until,
            next_dispatch_at=reserved_until,
            dispatches_without_claim=F("dispatches_without_claim") + 1,
            updated_at=DatabaseClock(),
        )
    )


@manage_publication_transaction
def confirm(database, *, token: uuid.UUID, task_id: uuid.UUID, redispatch_seconds: int) -> bool:
    if not lock_publication_task(get_publication_alias(database), task_id):
        return False
    records = TaskRecord.objects.using(get_publication_alias(database)).filter(
        pk=task_id,
        dispatch_token=token,
        status=TaskStatus.PENDING,
        held_at__isnull=True,
        dispatch_reserved_until__gt=DatabaseClock(),
    )
    return bool(
        records.update(
            last_dispatched_at=DatabaseClock(),
            next_dispatch_at=DatabaseClock() + timedelta(seconds=redispatch_seconds),
            dispatch_token=None,
            dispatch_reserved_until=None,
            updated_at=DatabaseClock(),
        )
    )


@manage_publication_transaction
def record_failure(
    database, *, token: uuid.UUID, task_id: uuid.UUID, backoff_seconds: float
) -> bool:
    if not lock_publication_task(get_publication_alias(database), task_id):
        return False
    return bool(
        TaskRecord.objects.using(get_publication_alias(database))
        .filter(pk=task_id, dispatch_token=token, status=TaskStatus.PENDING)
        .update(
            dispatch_token=None,
            dispatch_reserved_until=None,
            next_dispatch_at=DatabaseClock() + timedelta(seconds=backoff_seconds),
            updated_at=DatabaseClock(),
        )
    )


def lock_publication_task(database_alias, task_id):
    # Match claims' primary-row-first lock order before updating secondary
    # eligibility indexes; InnoDB may otherwise acquire those locks in reverse.
    records = TaskRecord.objects.using(database_alias).filter(pk=task_id)
    return lock_one(records) is not None


def set_transport_for_testing(transport: SqsTransport | None) -> None:
    """Replace the process-wide default transport (tests only)."""
    global _transport
    _transport = transport
