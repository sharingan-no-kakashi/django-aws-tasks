"""Reconciliation: expired-attempt recovery, holds, and due dispatch.

The sweeper runs bounded work in dependency order: recover expired leases,
escalate unclaimable work to a delivery hold, then dispatch due pending tasks.
Correctness never depends on a single scheduler invocation existing.
"""

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass

from django.db.models import Count, F, Min, Q

from django_tasks_aws import policies, telemetry
from django_tasks_aws.databases import DatabaseClock, context
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import lock_one, select_bounded_ids
from django_tasks_aws.databases.queries import get_due_pending_tasks
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import InvariantViolation
from django_tasks_aws.models import (
    AttemptOutcome,
    AttemptStatus,
    HoldReason,
    TaskAttempt,
    TaskRecord,
    TaskStatus,
)
from django_tasks_aws.schemas import RouteManifest
from django_tasks_aws.services import dispatch, invariants, outcomes
from django_tasks_aws.services.inspection import get_database_time
from django_tasks_aws.sqs import SqsTransport

logger = logging.getLogger(__name__)

DEFAULT_WORK_SECONDS = 45.0


@dataclass(frozen=True)
class ReconciliationReport:
    invariant_violations: int = 0
    recovered: int = 0
    finished: int = 0
    held: int = 0
    dispatched: int = 0
    dispatch_failed: int = 0
    oldest_due_age_seconds: float | None = None


def run_reconciliation(
    *,
    backend_alias: str = "default",
    database_alias: str,
    routes: RouteManifest,
    policy_catalog: policies.PolicyCatalog,
    transport: SqsTransport,
    recover_limit: int = 100,
    per_queue_limit: int = 100,
    dispatch: bool = True,
    deadline: float | None = None,
) -> ReconciliationReport:
    """Run one bounded reconciliation cycle."""
    deadline = deadline if deadline is not None else time.monotonic() + DEFAULT_WORK_SECONDS

    recovered = recover_expired_attempts(
        database_alias=database_alias,
        backend_alias=backend_alias,
        policy_catalog=policy_catalog,
        limit=recover_limit,
        deadline=deadline,
        queue_names=tuple(routes),
    )
    finished = finish_exhausted_pending(
        database_alias=database_alias,
        backend_alias=backend_alias,
        limit=recover_limit,
        deadline=deadline,
    )
    held = hold_delivery_exhausted(
        database_alias=database_alias,
        backend_alias=backend_alias,
        routes=routes,
        per_queue_limit=per_queue_limit,
        deadline=deadline,
    )
    notify_reconciliation(database_alias, backend_alias, deadline)
    dispatched, dispatch_failed = (
        dispatch_due(
            database_alias=database_alias,
            backend_alias=backend_alias,
            routes=routes,
            transport=transport,
            per_queue_limit=per_queue_limit,
            deadline=deadline,
        )
        if dispatch
        else (0, 0)
    )
    violations = invariants.find_violations(database_alias, backend_alias=backend_alias)
    report = ReconciliationReport(
        invariant_violations=len(violations),
        recovered=recovered,
        finished=finished,
        held=held,
        dispatched=dispatched,
        dispatch_failed=dispatch_failed,
        oldest_due_age_seconds=get_oldest_due_age(database_alias, routes, backend_alias),
    )
    report_sweep_health(report, database_alias, routes, backend_alias=backend_alias)
    return report


def notify_reconciliation(database_alias, backend_alias, deadline):
    from django_tasks_aws.workflows.adapter import uses_aws_workflows

    if not uses_aws_workflows():
        return
    from django_tasks_aws.workflows.aws import reconcile_after_tasks

    reconcile_after_tasks(
        sender=None, database_alias=database_alias, backend_alias=backend_alias, deadline=deadline
    )


def recover_expired_attempts(
    *,
    backend_alias: str = "default",
    database_alias: str,
    policy_catalog: policies.PolicyCatalog,
    limit: int,
    deadline: float | None = None,
    queue_names: tuple[str, ...] | None = None,
) -> int:
    ids = select_expired_task_ids(database_alias, backend_alias, queue_names, limit)
    recovered = 0
    for task_id in ids:
        if deadline is not None and time.monotonic() >= deadline:
            break
        recovered += int(recover_candidate(task_id, database_alias, backend_alias, policy_catalog))
    return recovered


@scoped(manage_coordination_transaction, "database_alias")
def select_expired_task_ids(database_alias, backend_alias, queue_names, limit):
    records = get_expired_tasks(database_alias, backend_alias)
    if queue_names is not None:
        records = records.filter(queue_name__in=queue_names)
    return select_bounded_ids(records.order_by("lease_expires_at", "pk"), limit)


def recover_candidate(task_id, database_alias, backend_alias, policy_catalog):
    try:
        return recover_one(
            task_id,
            database_alias=database_alias,
            backend_alias=backend_alias,
            policy_catalog=policy_catalog,
        )
    except InvariantViolation:
        telemetry.log_event(
            "invariant.violation",
            task_id=task_id,
            backend=backend_alias,
            error_type="InvariantViolation",
        )
        return False


def finish_exhausted_pending(
    *,
    database_alias: str,
    limit: int,
    deadline: float | None = None,
    backend_alias: str = "default",
) -> int:
    """Defensively finish pending tasks whose execution budget is already spent.

    Normal transitions never produce this state, but a policy change or a stale
    row could leave a pending task with ``attempts_started >= max_attempts`` that
    no claim or dispatch will ever pick up. Finishing it as failed keeps it from
    remaining permanently pending.
    """
    ids = select_exhausted_task_ids(database_alias, backend_alias, limit)
    finished = 0
    for task_id in ids:
        if deadline is not None and time.monotonic() >= deadline:
            break
        finished += int(finish_exhausted_task(task_id, database_alias, backend_alias))
    return finished


def get_exhausted_tasks(database_alias, backend_alias):
    return TaskRecord.objects.using(database_alias).filter(
        backend_alias=backend_alias,
        status=TaskStatus.PENDING,
        attempts_started__gte=F("max_attempts"),
    )


@scoped(manage_coordination_transaction, "database_alias")
def finish_exhausted_task(task_id, database_alias, backend_alias):
    records = get_exhausted_tasks(database_alias, backend_alias).filter(pk=task_id)
    record = lock_one(records)
    if record is None:
        return False
    records.update(
        status=TaskStatus.FAILED,
        last_error=build_exhausted_error(record),
        held_at=None,
        hold_reason=None,
        dispatch_token=None,
        dispatch_reserved_until=None,
        finished_at=DatabaseClock(),
        updated_at=DatabaseClock(),
    )
    return True


def build_exhausted_error(record):
    error = (
        record.last_error
        if record.last_error is not None
        else {
            "error_type": "AttemptBudgetExhausted",
            "message": "execution budget exhausted while pending",
            "uncertain": True,
        }
    )
    if record.held_at is not None:
        error = {
            **error,
            "previous_hold": {
                "held_at": record.held_at.isoformat(),
                "reason": record.hold_reason,
            },
        }
    return error


def get_expired_tasks(database_alias, backend_alias):
    return TaskRecord.objects.using(database_alias).filter(
        backend_alias=backend_alias,
        status=TaskStatus.RUNNING,
        lease_expires_at__lte=DatabaseClock(),
    )


def recover_one(
    task_id: str,
    *,
    database_alias: str,
    policy_catalog: policies.PolicyCatalog,
    backend_alias: str = "default",
) -> bool:
    recovery = recover_locked_attempt(task_id, database_alias, backend_alias, policy_catalog)
    if recovery is None:
        return False
    record, outcome = recovery
    report_recovery(record, outcome, database_alias)
    return True


def build_recovery_changes(record, policy_catalog, database_alias):
    policy = policy_catalog.get(record.task_name)
    if record.attempts_started >= record.max_attempts:
        return "Failed", {
            "status": TaskStatus.FAILED,
            "last_error": {"error_type": "LeaseExpired", "message": "abandoned", "uncertain": True},
            "finished_at": DatabaseClock(),
        }
    if policy.ambiguous_retry:
        retry_at = get_database_time(database_alias) + policies.compute_backoff(
            record.attempts_started
        )
        return "Retried", {
            "status": TaskStatus.PENDING,
            "available_at": retry_at,
            "next_dispatch_at": retry_at,
        }
    return "Held", {
        "status": TaskStatus.PENDING,
        "held_at": DatabaseClock(),
        "hold_reason": HoldReason.AMBIGUOUS_OUTCOME,
    }


def report_recovery(record, outcome, database_alias):
    telemetry.report_after_commit(
        database_alias,
        "attempt.abandoned",
        task_id=str(record.pk),
        attempt_id=record.lease_token,
        task_name=record.task_name,
        attempt_number=record.attempts_started,
        backend=record.backend_alias,
        metric_name="Abandoned",
        queue=record.queue_name,
    )
    outcomes.report_task_finalization(
        str(record.pk), record.lease_token, database_alias, queue=record.queue_name, outcome=outcome
    )


def hold_delivery_exhausted(
    *,
    backend_alias: str = "default",
    database_alias: str,
    routes: RouteManifest,
    per_queue_limit: int,
    deadline: float | None = None,
) -> int:
    held = 0
    for queue_name, route in routes.items():
        if route.paused:
            continue
        for task_id in select_due_task_ids(
            database_alias=database_alias,
            backend_alias=backend_alias,
            queue_name=queue_name,
            dispatch_limit=route.dispatch_limit,
            limit=per_queue_limit,
            only_exhausted=True,
        ):
            if deadline is not None and time.monotonic() >= deadline:
                return held
            if outcomes.hold_delivery_exhausted(
                task_id=task_id,
                dispatch_limit=route.dispatch_limit,
                database_alias=database_alias,
                backend_alias=backend_alias,
            ):
                held += 1
    return held


def dispatch_due(
    *,
    backend_alias: str = "default",
    database_alias: str,
    routes: RouteManifest,
    transport: SqsTransport,
    per_queue_limit: int,
    deadline: float | None = None,
) -> tuple[int, int]:
    """Visit each route in turn so failures and busy routes cannot monopolize a run."""
    pending = get_dispatch_candidates(database_alias, routes, per_queue_limit, backend_alias)
    dispatched = failed = 0
    while pending and (deadline is None or time.monotonic() < deadline):
        tasks = pending.popleft()
        task_id = tasks.popleft()
        if tasks:
            pending.append(tasks)
        sent, errors = publish_candidate(task_id, database_alias, routes, transport, backend_alias)
        dispatched += sent
        failed += errors
    return dispatched, failed


def get_dispatch_candidates(database_alias, routes, per_queue_limit, backend_alias):
    pending: deque[deque[str]] = deque()
    for queue_name, route in routes.items():
        if route.paused:
            continue
        tasks = select_due_task_ids(
            database_alias=database_alias,
            backend_alias=backend_alias,
            queue_name=queue_name,
            dispatch_limit=route.dispatch_limit,
            limit=per_queue_limit,
        )
        if tasks:
            pending.append(deque(tasks))
    return pending


def publish_candidate(task_id, database_alias, routes, transport, backend_alias) -> tuple[int, int]:
    try:
        result = dispatch.publish_task(
            task_id,
            transport=transport,
            database_alias=database_alias,
            backend_alias=backend_alias,
            routes=routes,
        )
    except Exception:
        # Publication is an independent repairable unit, including uncertain sends.
        logger.error("Task publication failed", extra={"task_id": task_id})
        return 0, 1
    return int(result is dispatch.DispatchResult.DISPATCHED), 0


@scoped(manage_coordination_transaction, "database_alias")
def select_due_task_ids(
    *,
    backend_alias: str = "default",
    database_alias: str,
    queue_name: str,
    dispatch_limit: int,
    limit: int,
    only_exhausted: bool = False,
) -> list[str]:
    """Select due, unheld, pending task IDs for a queue.

    ``only_exhausted`` selects tasks at or over the unclaimed-dispatch budget
    (candidates for a delivery hold); otherwise it selects dispatchable work.
    """
    records = get_due_pending_tasks(database_alias, backend_alias).filter(queue_name=queue_name)
    if only_exhausted:
        records = records.filter(dispatches_without_claim__gte=dispatch_limit)
    else:
        records = records.filter(dispatches_without_claim__lt=dispatch_limit)
    ids = records.order_by("next_dispatch_at").values_list("pk", flat=True)[:limit]
    return [str(task_id) for task_id in ids]


@scoped(manage_coordination_transaction, "database_alias")
def get_oldest_due_age(database_alias: str, routes=None, backend_alias="default") -> float | None:
    """Seconds since the oldest due, unheld, pending task became dispatchable."""
    records = TaskRecord.objects.using(database_alias).filter(
        backend_alias=backend_alias,
        status=TaskStatus.PENDING,
        held_at__isnull=True,
        next_dispatch_at__lte=DatabaseClock(),
        available_at__lte=DatabaseClock(),
    )
    if routes is not None:
        records = records.filter(
            queue_name__in=[name for name, route in routes.items() if not route.paused]
        )
    age = (
        records.annotate(due_age=DatabaseClock() - F("next_dispatch_at"))
        .order_by("next_dispatch_at")
        .values_list("due_age", flat=True)
        .first()
    )
    return age.total_seconds() if age is not None else None


def run_sweeper(*, deadline: float | None = None) -> dict:
    """Run one reconciliation cycle against the default backend and report it."""
    report = sweep_backend(deadline)
    return {
        "invariant_violations": report.invariant_violations,
        "recovered": report.recovered,
        "finished": report.finished,
        "held": report.held,
        "dispatched": report.dispatched,
        "dispatch_failed": report.dispatch_failed,
        "oldest_due_age_seconds": report.oldest_due_age_seconds,
    }


@scoped(manage_coordination_transaction, "database_alias")
def select_exhausted_task_ids(database_alias, backend_alias, limit):
    records = get_exhausted_tasks(database_alias, backend_alias)
    ids = list(records.values_list("pk", flat=True)[:limit])
    return ids


@scoped(manage_coordination_transaction, "database_alias")
def recover_locked_attempt(task_id, database_alias, backend_alias, policy_catalog):
    records = get_expired_tasks(database_alias, backend_alias).filter(pk=task_id)
    record = lock_one(records, skip_locked=True)
    if record is None:
        return None
    invariants.require_current_attempt(database_alias, task_id, record.lease_token)
    outcome, changes = build_recovery_changes(record, policy_catalog, database_alias)
    records.filter(lease_token=record.lease_token).update(
        lease_token=None, lease_expires_at=None, updated_at=DatabaseClock(), **changes
    )
    TaskAttempt.objects.using(database_alias).filter(
        pk=record.lease_token, task_id=record.pk
    ).update(
        status=AttemptStatus.ABANDONED,
        outcome=AttemptOutcome.UNKNOWN,
        finished_at=DatabaseClock(),
    )
    return (record, outcome)


@context.manage_invocation_connections()
def sweep_backend(deadline):
    from django_tasks_aws.configuration import get_invocation_backend
    from django_tasks_aws.sqs import BotoSqsTransport

    backend = get_invocation_backend()
    report = run_reconciliation(
        database_alias=backend.database_alias,
        backend_alias=backend.alias,
        routes=backend.routes,
        policy_catalog=backend.policies,
        transport=BotoSqsTransport(),
        deadline=deadline,
    )
    return report


def report_sweep_health(report, database_alias, routes, *, backend_alias="default"):
    now = get_database_time(database_alias)
    health = get_sweep_health(database_alias, routes, backend_alias, now)
    telemetry.log_event("sweep.completed", **asdict(report))
    telemetry.emit_metric("InvariantViolations", report.invariant_violations)
    telemetry.emit_metric("Heartbeat")
    telemetry.emit_metric("ExpiredLeases", health["expired"])
    telemetry.emit_metric("HeldBacklog", health["holds"])
    telemetry.emit_metric(
        "OldestHoldAge",
        max(0, (now - health["oldest_hold"]).total_seconds()) if health["oldest_hold"] else 0,
    )
    telemetry.emit_metric("OldestDueAge", report.oldest_due_age_seconds or 0)


def get_sweep_health(database_alias, routes, backend_alias, now):
    return (
        TaskRecord.objects.using(database_alias)
        .filter(
            queue_name__in=routes, backend_alias=backend_alias, status__in=["PENDING", "RUNNING"]
        )
        .aggregate(
            holds=Count("id", filter=Q(held_at__isnull=False)),
            oldest_hold=Min("held_at"),
            expired=Count("id", filter=Q(status="RUNNING", lease_expires_at__lte=now)),
        )
    )
