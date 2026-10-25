"""Audited operator transitions shared by admin and management commands."""

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from django_tasks_aws import telemetry
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import ControlConflict, ControlRejected, TaskResolutionError
from django_tasks_aws.import_paths import resolve_task
from django_tasks_aws.models import (
    ControlAction,
    HoldReason,
    TaskControlEvent,
    TaskRecord,
    TaskStatus,
)
from django_tasks_aws.serialization import dumps
from django_tasks_aws.services.inspection import get_database_time


@dataclass(frozen=True)
class ControlRequest:
    request_id: str
    reason: str
    actor_reference: str
    origin: str = "cli"
    actor_kind: str = "operator"
    queue_name: str | None = None
    expected_hold: str | None = None
    effects_reviewed: bool = False
    reset_delivery: bool = False

    def __post_init__(self):
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.request_id,
                self.reason,
                self.actor_reference,
                self.origin,
                self.actor_kind,
            )
        ):
            raise ControlRejected("Request identity, actor, origin, and reason are required")
        if len(self.request_id) > 200 or len(self.reason) > 2000 or len(self.actor_reference) > 256:
            raise ControlRejected("Control request text exceeds supported bounds")
        if type(self.effects_reviewed) is not bool or type(self.reset_delivery) is not bool:
            raise ControlRejected("Review and delivery-reset decisions must be booleans")


@dataclass(frozen=True)
class ControlResult:
    task_id: str
    action: str
    request_id: str
    replacement_task_id: str | None = None
    repeated: bool = False
    dry_run: bool = False


def operate(
    *, backend, task_id: str, action: str, request: ControlRequest, dry_run: bool = False
) -> ControlResult:
    action = ControlAction(action)
    task_id = str(UUID(str(task_id)))
    if dry_run:
        return preview_action(backend, task_id, action, request)
    result = apply_control(backend, task_id, action, request)
    if result.repeated:
        return result
    telemetry.report_after_commit(
        backend.database_alias,
        "control.accepted",
        metric_name="ControlActions",
        task_id=task_id,
        action=action,
        request_id=request.request_id,
        backend=backend.alias,
    )
    return result


def find_task_record(backend, task_id, *, lock=False):
    records = TaskRecord.objects.using(backend.database_alias).filter(backend_alias=backend.alias)
    if lock:
        records = records.select_for_update()
    try:
        return records.get(pk=task_id)
    except TaskRecord.DoesNotExist as error:
        raise ControlRejected(f"Task {task_id} does not exist on this backend") from error


def compute_control_intent(action, request):
    return sha256(
        dumps(
            {
                "action": action,
                "queue_name": request.queue_name,
                "expected_hold": request.expected_hold,
                "effects_reviewed": request.effects_reviewed,
                "reset_delivery": request.reset_delivery,
                "reason": request.reason,
                "actor": [request.actor_kind, request.actor_reference, request.origin],
            }
        ).encode()
    ).hexdigest()


def get_previous_control_result(backend, task_id, action, request):
    event = (
        TaskControlEvent.objects.using(backend.database_alias)
        .filter(
            request_id=request.request_id,
            target_scope=task_id,
        )
        .first()
    )
    if event is None:
        return None
    if event.intent_hash != compute_control_intent(action, request):
        raise ControlConflict("Request identity was already used with different intent")
    replacement = str(event.replacement_task_id) if event.replacement_task_id else None
    return ControlResult(task_id, action, request.request_id, replacement, repeated=True)


def preview_action(backend, task_id, action, request):
    record = find_task_record(backend, task_id)
    prior = get_previous_control_result(backend, task_id, action, request)
    if prior is not None:
        return replace(prior, dry_run=True)
    validate(record, action, request, backend)
    return ControlResult(task_id, action, request.request_id, dry_run=True)


def validate(record, action, request, backend):
    if (
        request.queue_name
        and action != ControlAction.REPLAY
        and request.queue_name != record.queue_name
    ):
        raise ControlRejected("Only replay can change a task's destination")
    match action:
        case ControlAction.INVARIANT_REVIEW:
            from django_tasks_aws.services.invariants import find_violations

            if not find_violations(
                backend.database_alias, backend_alias=backend.alias, task_id=str(record.id)
            ):
                raise ControlRejected("Task has no detected invariant violation to review")
        case ControlAction.REPLAY:
            validate_replay(record, request, backend)
        case ControlAction.RELEASE:
            validate_release(record, request, backend)
        case ControlAction.DISPATCH:
            validate_compatibility(record, record.queue_name, backend)
            if record.status != TaskStatus.PENDING or record.held_at:
                raise ControlRejected("Only unheld pending work can be dispatched")
            if (
                record.available_at > get_database_time(backend.database_alias)
                or record.attempts_started >= record.max_attempts
            ):
                raise ControlRejected("Task is not due or its attempt budget is exhausted")
            if (
                record.dispatch_reserved_until
                and record.dispatch_reserved_until > get_database_time(backend.database_alias)
            ):
                raise ControlRejected("Task has a live publication reservation")
            if record.dispatches_without_claim >= backend.routes[record.queue_name].dispatch_limit:
                raise ControlRejected("Delivery budget is exhausted; review and release its hold")
        case ControlAction.RECONCILE:
            validate_recovery(record, backend)
        case _:
            raise ControlRejected("Unsupported control action")


def validate_recovery(record, backend):
    now = get_database_time(backend.database_alias)
    if record.status == TaskStatus.RUNNING and record.lease_expires_at <= now:
        return
    if (
        record.status == TaskStatus.PENDING
        and not record.held_at
        and record.available_at <= now
        and record.next_dispatch_at <= now
    ):
        if record.attempts_started >= record.max_attempts:
            return
        if record.dispatches_without_claim >= backend.routes[record.queue_name].dispatch_limit:
            return
    raise ControlRejected("Only expired attempts or exhausted pending work can be recovered")


def validate_replay(record, request, backend):
    if record.status not in (TaskStatus.FAILED, TaskStatus.SUCCESSFUL):
        raise ControlRejected("Replay requires terminal work")
    if (
        record.details_expired_at
        or record.finished_at < get_database_time(backend.database_alias) - backend.result_retention
    ):
        raise ControlRejected("Replay details have expired")
    if not request.effects_reviewed:
        raise ControlRejected("Replay requires explicit review of possible business effects")
    validate_compatibility(record, request.queue_name or record.queue_name, backend)


def validate_release(record, request, backend):
    if record.status != TaskStatus.PENDING or record.held_at is None:
        raise ControlRejected("Task does not have a pending hold")
    if request.expected_hold and request.expected_hold != record.held_at.isoformat():
        raise ControlConflict("The task's hold changed after inspection")
    if record.attempts_started >= record.max_attempts:
        raise ControlRejected("Execution budget is exhausted")
    if record.hold_reason == HoldReason.AMBIGUOUS_OUTCOME and not request.effects_reviewed:
        raise ControlRejected("Ambiguous effects require explicit operator review")
    if (
        not request.reset_delivery
        and record.dispatches_without_claim >= backend.routes[record.queue_name].dispatch_limit
    ):
        raise ControlRejected("Release must explicitly reset the exhausted delivery budget")
    validate_compatibility(record, record.queue_name, backend)


def validate_compatibility(record, queue_name, backend):
    policy = backend.policies.get(record.task_name)
    if queue_name not in backend.routes or not policy.allows_route(queue_name):
        raise ControlRejected("Destination is not allowed by the current task policy")
    if queue_name in backend.paused_routes or not policy.supports_version(record.payload_version):
        raise ControlRejected("Route is paused or payload version is unsupported")
    try:
        resolve_task(record.task_name, allowed_modules=backend.allowed_task_modules)
    except TaskResolutionError as error:
        raise ControlRejected("Stored task cannot be resolved by this deployment") from error


def apply(record, action, request, backend):
    match action:
        case ControlAction.INVARIANT_REVIEW:
            return None
        case ControlAction.REPLAY:
            return replay(record, request, backend)
        case ControlAction.RELEASE:
            release(record, request, backend)
        case ControlAction.DISPATCH:
            record.next_dispatch_at = get_database_time(backend.database_alias)
            record.save(
                using=backend.database_alias, update_fields=["next_dispatch_at", "updated_at"]
            )
            dispatch_after_commit(record, backend, force=True)
        case ControlAction.RECONCILE:
            recover(record, backend)
    return None


def recover(record, backend):
    from django_tasks_aws.services import outcomes
    from django_tasks_aws.services.reconcile import recover_one

    if record.status == TaskStatus.RUNNING:
        changed = recover_one(
            record.id,
            database_alias=backend.database_alias,
            backend_alias=backend.alias,
            policy_catalog=backend.policies,
        )
    elif record.attempts_started >= record.max_attempts:
        record.status = TaskStatus.FAILED
        record.finished_at = get_database_time(backend.database_alias)
        record.dispatch_token = record.dispatch_reserved_until = None
        record.last_error = record.last_error or {
            "error_type": "AttemptBudgetExhausted",
            "uncertain": True,
        }
        record.save(using=backend.database_alias)
        changed = True
    else:
        changed = outcomes.hold_delivery_exhausted(
            task_id=str(record.id),
            dispatch_limit=backend.routes[record.queue_name].dispatch_limit,
            database_alias=backend.database_alias,
            backend_alias=backend.alias,
        )
    if not changed:
        raise ControlRejected("Recovery eligibility changed; inspect current state")


def release(record, request, backend):
    record.held_at = record.hold_reason = None
    record.dispatch_token = record.dispatch_reserved_until = None
    record.next_dispatch_at = max(get_database_time(backend.database_alias), record.available_at)
    if request.reset_delivery:
        record.dispatches_without_claim = 0
    elif record.dispatches_without_claim >= backend.routes[record.queue_name].dispatch_limit:
        raise ControlRejected("Release must explicitly reset the exhausted delivery budget")
    record.save(using=backend.database_alias)
    dispatch_after_commit(record, backend)


def replay(record, request, backend):
    now = get_database_time(backend.database_alias)
    replacement = TaskRecord.objects.using(backend.database_alias).create(
        task_name=record.task_name,
        backend_alias=record.backend_alias,
        payload_version=record.payload_version,
        payload=record.payload,
        payload_hash=record.payload_hash,
        task_options=record.task_options,
        queue_name=request.queue_name or record.queue_name,
        available_at=now,
        next_dispatch_at=now,
        max_attempts=backend.policies.get(record.task_name).max_attempts,
        dedupe_scope=f"control:{record.id}",
        dedupe_key=request.request_id,
        operation_key=record.operation_key,
        producer_version=backend.producer_version,
        replay_of_id=record.id,
    )
    dispatch_after_commit(replacement, backend)
    return str(replacement.id)


def dispatch_after_commit(record, backend, *, force=False):
    from django_tasks_aws.services.enqueue import register_task_dispatch

    if force or backend.immediate_dispatch:
        register_task_dispatch(
            str(record.id), backend.database_alias, backend.routes, backend.alias
        )


def audit(record, action, request, before, replacement, backend):
    TaskControlEvent.objects.using(backend.database_alias).create(
        request_id=request.request_id,
        target_scope=str(record.id),
        action=action,
        intent_hash=compute_control_intent(action, request),
        task_id=record.id,
        replacement_task_id=replacement,
        actor_kind=request.actor_kind,
        actor_reference=request.actor_reference,
        origin=request.origin,
        reason=request.reason,
        before_summary=before,
        after_summary=build_control_summary(record),
    )


def build_control_summary(record):
    return {
        name: value.isoformat() if isinstance(value, datetime) else value
        for name, value in {
            "status": record.status,
            "held_at": record.held_at,
            "hold_reason": record.hold_reason,
            "queue_name": record.queue_name,
            "attempts_started": record.attempts_started,
            "dispatches_without_claim": record.dispatches_without_claim,
            "available_at": record.available_at,
            "next_dispatch_at": record.next_dispatch_at,
        }.items()
    }


@scoped(manage_coordination_transaction, "backend.database_alias")
def apply_control(backend, task_id, action, request):
    record = find_task_record(backend, task_id, lock=True)
    prior = get_previous_control_result(backend, task_id, action, request)
    if prior is not None:
        return prior
    validate(record, action, request, backend)
    before = build_control_summary(record)
    replacement = apply(record, action, request, backend)
    record.refresh_from_db()
    audit(record, action, request, before, replacement, backend)
    return ControlResult(task_id, action, request.request_id, replacement)
