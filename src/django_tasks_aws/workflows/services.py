"""Durable acceptance and idempotent, bounded workflow advancement."""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from django_tasks_aws import telemetry
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.decorators import scoped
from django_tasks_aws.services.enqueue import defer_publication

from .adapter import get_task_adapter
from .configuration import get_workflow_limits
from .definition import compile_graph
from .exceptions import (
    PreparationError,
    WorkflowConflict,
    WorkflowError,
    WorkflowNotFound,
    WorkflowRecoveryError,
)
from .models import (
    NodeStatus,
    RunStatus,
    WorkflowAction,
    WorkflowActionType,
    WorkflowDependency,
    WorkflowNode,
    WorkflowRun,
)
from .observability import report_advancement
from .values import resolve_value, serialize_canonical_json

TERMINAL_NODES = {NodeStatus.SUCCESSFUL, NodeStatus.FAILED, NodeStatus.SKIPPED}
ACTIVE_RUNS = (RunStatus.RUNNING, RunStatus.BLOCKED)
logger = logging.getLogger(__name__)


def submit_workflow(graph, *, submission_key=None, name=None, business_reference=None):
    if submission_key is not None and (
        not isinstance(submission_key, str) or not submission_key or len(submission_key) > 255
    ):
        raise WorkflowError("submission_key must be a nonempty string of at most 255 characters")
    name = validate_display_name(name, "Workflow name")
    business_reference = validate_display_name(
        business_reference, "Business reference", allow_empty=True
    )
    definition = compile_graph(graph)
    return accept_workflow_definition(definition, submission_key, name, business_reference)


def validate_display_name(value, label, *, allow_empty=False):
    if value is None:
        return ""
    if not isinstance(value, str) or (not value.strip() and not allow_empty) or len(value) > 200:
        raise WorkflowError(f"{label} must be a string of at most 200 characters")
    return value


def accept_run(definition, submission_key, name, business_reference):
    records = WorkflowRun.objects.using(definition.database_alias)
    try:
        return create_workflow_run(records, definition, submission_key, name, business_reference)
    except IntegrityError:
        if submission_key is None:
            raise
        run = records.get(backend_alias=definition.backend_alias, submission_key=submission_key)
        if run.definition_hash != definition.digest:
            raise WorkflowConflict(
                "Submission key already identifies a different workflow"
            ) from None
        return run, False


def save_graph(run, definition, alias):
    WorkflowNode.objects.using(alias).bulk_create(
        [
            WorkflowNode(run=run, position=index, name=definition.names[index], **node)
            for index, node in enumerate(definition.nodes)
        ]
    )
    # Not every supported database returns identities from bulk_create.
    nodes = {node.key: node for node in run.nodes.using(alias).all()}
    WorkflowDependency.objects.using(alias).bulk_create(
        [
            WorkflowDependency(run=run, parent=nodes[parent], child=nodes[child])
            for parent, child in definition.edges
        ]
    )


def advance_workflow(workflow_id, *, database_alias="default", deadline=None):
    """Serialize one pass; execution and publication occur outside the workflow lock."""
    if not has_time_available(deadline):
        telemetry.log_event("workflow.deferred", workflow_id=str(workflow_id))
        telemetry.emit_metric("WorkflowDeferred")
        return None
    return advance_workflow_locked(workflow_id, database_alias, deadline)


def advance_locked(run, adapter, alias, deadline=None):
    nodes = list(run.nodes.using(alias).all())
    snapshots = adapter.get_snapshots([node.task_id for node in nodes if node.task_id is not None])
    parents = build_parent_map(run, nodes, alias)
    refresh_nodes(nodes, snapshots)
    skip_failed_descendants(nodes, parents)
    if run.cancel_requested_at is not None:
        for node in nodes:
            cancel_node(node, snapshots.get(str(node.task_id)), adapter, alias)
    else:
        schedule_ready_nodes(nodes, parents, snapshots, adapter, alias, deadline)
    report_preparation_failures(run, nodes)
    report_advancement(run, nodes, parents, snapshots)
    WorkflowNode.objects.using(alias).bulk_update(nodes, ["status", "task_id", "error"])
    update_run(run, nodes, alias)


def report_preparation_failures(run, nodes):
    for node in nodes:
        if node.status == NodeStatus.FAILED and node.error.get("error_type") == "PreparationError":
            telemetry.log_event(
                "workflow.preparation_failure",
                workflow_id=str(run.pk),
                step_key=node.key,
                error_type="PreparationError",
            )


def build_parent_map(run, nodes, alias):
    by_id = {node.pk: node for node in nodes}
    parents: dict[str, list[WorkflowNode]] = {node.key: [] for node in nodes}
    for parent_id, child_id in run.dependencies.using(alias).values_list("parent_id", "child_id"):
        parents[by_id[child_id].key].append(by_id[parent_id])
    return parents


def refresh_nodes(nodes, snapshots):
    for node in nodes:
        if node.error.get("error_type") == "Cancelled":
            continue
        if node.task_id is None:
            continue
        snapshot = snapshots.get(str(node.task_id))
        if snapshot is None:
            node.status = NodeStatus.FAILED
            node.error = {"error_type": "MissingTask", "message": "Associated task is missing"}
        else:
            node.status = snapshot.status


def skip_failed_descendants(nodes, parents):
    changed = True
    while changed:
        changed = False
        for node in nodes:
            failed = any(parent.status in {"FAILED", "SKIPPED"} for parent in parents[node.key])
            if node.status == NodeStatus.WAITING and failed:
                node.status = NodeStatus.SKIPPED
                node.error = {"error_type": "DependencyFailed"}
                changed = True


def schedule_ready_nodes(nodes, parents, snapshots, adapter, alias, deadline=None):
    results = {
        node.key: snapshots[str(node.task_id)].result
        for node in nodes
        if node.status == NodeStatus.SUCCESSFUL
    }
    remaining = get_workflow_limits().advancement_batch
    for node in nodes:
        if not has_time_available(deadline):
            break
        ready = all(parent.status == NodeStatus.SUCCESSFUL for parent in parents[node.key])
        if node.status != NodeStatus.WAITING or not ready or remaining == 0:
            continue
        schedule_node(node, results, adapter, alias)
        remaining -= 1
    skip_failed_descendants(nodes, parents)


def schedule_node(node, results, adapter, alias):
    try:
        arguments = resolve_value(node.arguments, results)
        enqueue_workflow_node(node, arguments, adapter, alias)
        node.status = NodeStatus.READY
    except PreparationError as error:
        node.status = NodeStatus.FAILED
        node.error = {"error_type": "PreparationError", "message": str(error)}


def update_run(run, nodes, alias):
    if run.cancel_requested_at is not None:
        if all(node.status in TERMINAL_NODES for node in nodes):
            run.status = RunStatus.CANCELLED
            run.finished_at = run.finished_at or timezone.now()
        else:
            run.status = RunStatus.RUNNING
    else:
        finished = all(node.status in TERMINAL_NODES for node in nodes)
        if finished:
            succeeded = all(node.status == NodeStatus.SUCCESSFUL for node in nodes)
            run.status = RunStatus.SUCCESSFUL if succeeded else RunStatus.FAILED
            run.finished_at = timezone.now()
        else:
            blocked = any(node.status == NodeStatus.BLOCKED for node in nodes)
            run.status = RunStatus.BLOCKED if blocked else RunStatus.RUNNING
    run.last_reconciled_at = timezone.now()
    run.save(
        using=alias,
        update_fields=[
            "status",
            "finished_at",
            "last_reconciled_at",
            "updated_at",
        ],
    )


def reconcile_workflows(*, database_alias, backend_alias="default", limit=None, deadline=None):
    """Visit the least recently reconciled runs first, including operator-blocked runs."""
    limit = get_workflow_limits().sweep_limit if limit is None else limit
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise WorkflowError("Workflow reconciliation limit must be positive")
    if not has_time_available(deadline):
        return 0
    telemetry.emit_metric("WorkflowCoordinatorHeartbeat")
    runs = (
        WorkflowRun.objects.using(database_alias)
        .filter(
            backend_alias=backend_alias,
            status__in=ACTIVE_RUNS,
        )
        .order_by(F("last_reconciled_at").asc(nulls_first=True), "created_at", "id")
    )
    identities = select_workflow_ids(runs, limit, database_alias)
    processed = 0
    for run_id in identities:
        if not has_time_available(deadline):
            break
        processed += int(reconcile_candidate(run_id, database_alias, deadline))
    telemetry.emit_metric("WorkflowVisited", processed)
    return processed


def has_time_available(deadline):
    return (
        deadline is None
        or deadline - time.monotonic() > get_workflow_limits().minimum_remaining_seconds
    )


def reconcile_candidate(run_id, alias, deadline=None):
    try:
        return advance_workflow(run_id, database_alias=alias, deadline=deadline) is not None
    except (DatabaseError, WorkflowError):
        telemetry.emit_metric("WorkflowReconcileErrors")
        logger.warning("Workflow reconciliation failed", extra={"workflow_id": str(run_id)})
        telemetry.log_event("workflow.reconcile_error", workflow_id=str(run_id))
    except Exception:
        logger.exception(
            "Unexpected workflow reconciliation error", extra={"workflow_id": str(run_id)}
        )
        telemetry.log_event(
            "workflow.reconcile_error", workflow_id=str(run_id), error_type="unexpected"
        )
    defer_reconcile(run_id, alias)
    return False


def defer_reconcile(run_id, alias):
    WorkflowRun.objects.using(alias).filter(pk=run_id).update(last_reconciled_at=timezone.now())


def expire_workflows(*, database_alias, backend_alias="default", limit=None, dry_run=False):
    """Redact terminal workflow inputs while retaining graph and submission identities."""
    limits = get_workflow_limits()
    limit = limits.sweep_limit if limit is None else limit
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise WorkflowError("Workflow cleanup limit must be positive")
    cutoff = timezone.now() - timedelta(days=limits.retention_days)
    records = WorkflowRun.objects.using(database_alias).filter(
        backend_alias=backend_alias,
        status__in=[RunStatus.SUCCESSFUL, RunStatus.FAILED, RunStatus.CANCELLED],
        details_expired_at__isnull=True,
        finished_at__lt=cutoff,
    )
    ids = list(records.order_by("finished_at").values_list("pk", flat=True)[:limit])
    if dry_run:
        return len(ids)
    return sum(expire_workflow(identity, database_alias, cutoff) for identity in ids)


@scoped(transaction.atomic, using="alias")
def expire_workflow(identity, alias, cutoff):
    run = WorkflowRun.objects.using(alias).select_for_update().get(pk=identity)
    if (
        run.details_expired_at is not None
        or run.status in ACTIVE_RUNS
        or run.finished_at is None
        or run.finished_at >= cutoff
    ):
        return False
    run.nodes.using(alias).update(arguments=[], error={})
    run.actions.using(alias).update(
        reason="", actor_reference="", before_summary={}, after_summary={}
    )
    run.details_expired_at = timezone.now()
    run.save(using=alias, update_fields=["details_expired_at"])
    return True


@dataclass(frozen=True)
class RetryOutcome:
    run_id: str
    node_key: str
    replacement_task_id: str | None
    repeated: bool
    dry_run: bool = False


def validate_metadata(request_id, reason, actor_reference, actor_kind, origin):
    for label, value, maximum in (
        ("request_id", request_id, 200),
        ("reason", reason, 2000),
        ("actor_reference", actor_reference, 200),
        ("actor_kind", actor_kind, 64),
        ("origin", origin, 64),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise WorkflowRecoveryError(f"{label} must contain 1–{maximum} characters")


def get_recovery_run(workflow_id, backend_alias, alias):
    try:
        return (
            WorkflowRun.objects.using(alias)
            .select_for_update()
            .get(pk=workflow_id, backend_alias=backend_alias)
        )
    except WorkflowRun.DoesNotExist as error:
        raise WorkflowNotFound(str(workflow_id)) from error


def get_previous_action(run, request_id, alias, intent):
    action = WorkflowAction.objects.using(alias).filter(run=run, request_id=request_id).first()
    if action is not None and action.intent_hash != intent:
        raise WorkflowConflict("Recovery request ID already used with different intent")
    return action


def validate_recoverable(run):
    if run.cancel_requested_at is not None:
        raise WorkflowRecoveryError("Cancelled workflows cannot be retried")
    if run.details_expired_at is not None:
        raise WorkflowRecoveryError("Workflow recovery details have expired")
    if run.definition_version != 1:
        raise WorkflowRecoveryError("Unsupported saved workflow definition version")


def retry_workflow_step(
    workflow_id,
    node_key,
    *,
    request_id,
    reason,
    actor_reference,
    database_alias="default",
    backend_alias="default",
    actor_kind="operator",
    origin="cli",
    effects_reviewed=False,
    dry_run=False,
):
    """Audited, idempotent retry of one failed step and its skipped descendants."""
    validate_metadata(request_id, reason, actor_reference, actor_kind, origin)
    if type(effects_reviewed) is not bool or type(dry_run) is not bool:
        raise WorkflowRecoveryError("Review and dry-run decisions must be booleans")
    intent = compute_action_intent(
        "retry", node_key, effects_reviewed, reason, actor_reference, actor_kind, origin
    )
    return retry_workflow_locked(
        workflow_id,
        node_key,
        request_id,
        reason,
        actor_reference,
        database_alias,
        backend_alias,
        actor_kind,
        origin,
        effects_reviewed,
        dry_run,
        intent,
    )


def require_same_database(adapter, database_alias):
    if adapter.database_alias != database_alias:
        raise WorkflowError("Workflow backend database changed after submission")


def get_retry_node(run, node_key, alias):
    try:
        return run.nodes.using(alias).get(key=node_key)
    except WorkflowNode.DoesNotExist as error:
        raise WorkflowRecoveryError(f"No step named {node_key!r}") from error


def get_previous_retry(run, request_id, alias, intent):
    action = get_previous_action(run, request_id, alias, intent)
    if action is None:
        return None
    node_key = action.target_node.key if action.target_node is not None else ""
    replacement = str(action.replacement_task_id) if action.replacement_task_id else None
    return RetryOutcome(str(run.pk), node_key, replacement, True)


def validate_retry(node, adapter, effects_reviewed, alias):
    if node.status != NodeStatus.FAILED:
        raise WorkflowRecoveryError("Only a failed step can be retried")
    if (
        node.task_id is not None
        and adapter.is_failure_uncertain(node.task_id)
        and not effects_reviewed
    ):
        raise WorkflowRecoveryError("The failed effect is uncertain; review before retrying")
    parents = node.incoming.using(alias).values_list("parent__key", "parent__task_id")
    missing = [
        key
        for key, task_id in parents
        if task_id is None or not adapter.is_result_available(task_id)
    ]
    if missing:
        raise WorkflowRecoveryError(f"Parent results expired for: {', '.join(missing)}")


def get_successful_results(nodes, adapter, alias):
    snapshots = adapter.get_snapshots(
        [node.task_id for node in nodes if node.status == NodeStatus.SUCCESSFUL]
    )
    return {
        node.key: snapshots[str(node.task_id)].result
        for node in nodes
        if node.status == NodeStatus.SUCCESSFUL
    }


def apply_retry(run, node, nodes, parents, replacement_id, alias):
    by_key = {node.key: node for node in nodes}
    node.task_id = replacement_id
    node.status = NodeStatus.READY
    node.error = {}
    node.save(using=alias, update_fields=["task_id", "status", "error"])
    for key in get_descendant_keys(node.key, parents):
        descendant = by_key[key]
        if descendant.status == NodeStatus.SKIPPED:
            descendant.status = NodeStatus.WAITING
            descendant.error = {}
            descendant.save(using=alias, update_fields=["status", "error"])
    resume_run(run, alias)


def get_descendant_keys(root_key, parents):
    children: dict[str, list[str]] = {key: [] for key in parents}
    for child_key, parent_nodes in parents.items():
        for parent in parent_nodes:
            children[parent.key].append(child_key)
    seen: set[str] = set()
    pending = [root_key]
    while pending:
        key = pending.pop()
        for child_key in children[key]:
            if child_key not in seen:
                seen.add(child_key)
                pending.append(child_key)
    return seen


def resume_run(run, alias):
    if run.status not in ACTIVE_RUNS:
        run.status = RunStatus.RUNNING
        run.finished_at = None
    run.last_reconciled_at = timezone.now()
    run.save(
        using=alias, update_fields=["status", "finished_at", "last_reconciled_at", "updated_at"]
    )


def record_action(
    run,
    node,
    request_id,
    reason,
    actor_reference,
    actor_kind,
    origin,
    alias,
    *,
    original_task_id,
    replacement_id,
    effects_reviewed,
    intent,
):
    WorkflowAction.objects.using(alias).create(
        run=run,
        action=WorkflowActionType.RETRY,
        request_id=request_id,
        intent_hash=intent,
        target_node=node,
        original_task_id=original_task_id,
        replacement_task_id=replacement_id,
        actor_kind=actor_kind,
        actor_reference=actor_reference,
        origin=origin,
        reason=reason,
        before_summary={"status": NodeStatus.FAILED},
        after_summary={"status": NodeStatus.READY},
    )


def compute_action_intent(action, node_key, effects_reviewed, reason, actor, actor_kind, origin):
    return hashlib.sha256(
        serialize_canonical_json(
            {
                "action": action,
                "node": node_key,
                "effects_reviewed": effects_reviewed,
                "reason": reason,
                "actor": actor,
                "actor_kind": actor_kind,
                "origin": origin,
            }
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CancelOutcome:
    run_id: str
    settled: bool
    repeated: bool
    dry_run: bool = False


def cancel_workflow(
    workflow_id,
    *,
    request_id,
    reason,
    actor_reference,
    database_alias="default",
    backend_alias="default",
    actor_kind="operator",
    origin="cli",
    dry_run=False,
):
    """Audited, idempotent cancellation that stops scheduling and preserves running leases."""
    validate_metadata(request_id, reason, actor_reference, actor_kind, origin)
    if type(dry_run) is not bool:
        raise WorkflowRecoveryError("dry_run must be a boolean")
    intent = compute_action_intent("cancel", "", False, reason, actor_reference, actor_kind, origin)
    return cancel_workflow_locked(
        workflow_id,
        request_id,
        reason,
        actor_reference,
        database_alias,
        backend_alias,
        actor_kind,
        origin,
        dry_run,
        intent,
    )


def get_previous_cancel(run, request_id, alias, intent):
    action = get_previous_action(run, request_id, alias, intent)
    if action is None:
        return None
    return CancelOutcome(str(run.pk), run.status == RunStatus.CANCELLED, True)


def validate_cancel(run):
    if run.status in (RunStatus.SUCCESSFUL, RunStatus.FAILED, RunStatus.CANCELLED):
        raise WorkflowRecoveryError("Only an active workflow can be cancelled")


def apply_cancel(run, adapter, alias):
    run.cancel_requested_at = timezone.now()
    run.save(using=alias, update_fields=["cancel_requested_at", "updated_at"])
    nodes = list(run.nodes.using(alias).all())
    snapshots = adapter.get_snapshots([node.task_id for node in nodes if node.task_id is not None])
    running = False
    for node in nodes:
        snapshot = snapshots.get(str(node.task_id)) if node.task_id is not None else None
        if cancel_node(node, snapshot, adapter, alias):
            running = True
    settled = not running
    run.status = RunStatus.CANCELLED if settled else RunStatus.RUNNING
    run.finished_at = timezone.now() if settled else None
    run.save(using=alias, update_fields=["status", "finished_at", "updated_at"])
    return settled


def cancel_node(node, snapshot, adapter, alias):
    status = snapshot.status if snapshot is not None else node.status
    if status == NodeStatus.READY and node.task_id is not None and not adapter.hold(node.task_id):
        latest = adapter.get_snapshots([node.task_id]).get(str(node.task_id))
        if latest is None:
            return True
        status = latest.status
    if status == NodeStatus.RUNNING:
        node.status = NodeStatus.RUNNING
        node.save(using=alias, update_fields=["status"])
        return True
    if status in (NodeStatus.WAITING, NodeStatus.READY, NodeStatus.BLOCKED):
        node.status = NodeStatus.SKIPPED
        node.error = {"error_type": "Cancelled"}
        node.save(using=alias, update_fields=["status", "error"])
    elif status in TERMINAL_NODES:
        node.status = status
        node.save(using=alias, update_fields=["status"])
    return False


def record_cancel(
    run, request_id, reason, actor_reference, actor_kind, origin, alias, intent, before
):
    WorkflowAction.objects.using(alias).create(
        run=run,
        action=WorkflowActionType.CANCEL,
        request_id=request_id,
        intent_hash=intent,
        target_node=None,
        actor_kind=actor_kind,
        actor_reference=actor_reference,
        origin=origin,
        reason=reason,
        before_summary={"status": before},
        after_summary={"status": run.status},
    )


@scoped(transaction.atomic, using="definition.database_alias")
def accept_workflow_definition(definition, submission_key, name, business_reference):
    alias = definition.database_alias
    run, created = accept_run(definition, submission_key, name, business_reference)
    if created:
        save_graph(run, definition, alias)
        advance_locked(run, get_task_adapter(run.backend_alias), alias)
    telemetry.report_after_commit(
        alias,
        "workflow.accepted",
        workflow_id=str(run.pk),
        run_status=run.status,
        repeated=not created,
    )
    return run


@scoped(transaction.atomic, using="definition.database_alias")
def create_workflow_run(records, definition, submission_key, name, business_reference):
    return records.create(
        backend_alias=definition.backend_alias,
        submission_key=submission_key,
        name=name,
        business_reference=business_reference,
        definition_hash=definition.digest,
    ), True


@scoped(manage_coordination_transaction, "database_alias")
@scoped(defer_publication, lambda arguments: arguments["deadline"] is not None)
def advance_workflow_locked(workflow_id, database_alias, deadline):
    records = WorkflowRun.objects.using(database_alias).select_for_update(skip_locked=True)
    try:
        run = records.get(pk=workflow_id)
    except WorkflowRun.DoesNotExist:
        return None
    if run.status not in ACTIVE_RUNS:
        return run
    if run.definition_version != 1:
        raise WorkflowError("Unsupported saved workflow definition version")
    adapter = get_task_adapter(run.backend_alias)
    if adapter.database_alias != database_alias:
        raise WorkflowError("Workflow backend database changed after submission")
    advance_locked(run, adapter, database_alias, deadline)
    return run


@scoped(manage_coordination_transaction, "database_alias")
def select_workflow_ids(runs, limit, database_alias):
    identities = list(runs.values_list("pk", flat=True)[:limit])
    return identities


@scoped(transaction.atomic, using="alias")
def enqueue_workflow_node(node, arguments, adapter, alias):
    node.task_id = adapter.enqueue(node, arguments["args"], arguments["kwargs"])


@scoped(transaction.atomic, using="database_alias")
def retry_workflow_locked(
    workflow_id,
    node_key,
    request_id,
    reason,
    actor_reference,
    database_alias,
    backend_alias,
    actor_kind,
    origin,
    effects_reviewed,
    dry_run,
    intent,
):
    run = get_recovery_run(workflow_id, backend_alias, database_alias)
    adapter = get_task_adapter(run.backend_alias)
    require_same_database(adapter, database_alias)
    previous = get_previous_retry(run, request_id, database_alias, intent)
    if previous is not None:
        return previous
    validate_recoverable(run)
    node = get_retry_node(run, node_key, database_alias)
    validate_retry(node, adapter, effects_reviewed, database_alias)
    if dry_run:
        return RetryOutcome(str(run.pk), node.key, None, False, dry_run=True)
    nodes = list(run.nodes.using(database_alias).all())
    parents = build_parent_map(run, nodes, database_alias)
    results = get_successful_results(nodes, adapter, database_alias)
    arguments = resolve_value(node.arguments, results)
    original_task_id = node.task_id
    replacement_id = adapter.retry(
        node,
        arguments["args"],
        arguments["kwargs"],
        request_id=request_id,
    )
    apply_retry(run, node, nodes, parents, replacement_id, database_alias)
    record_action(
        run,
        node,
        request_id,
        reason,
        actor_reference,
        actor_kind,
        origin,
        database_alias,
        original_task_id=original_task_id,
        replacement_id=replacement_id,
        effects_reviewed=effects_reviewed,
        intent=intent,
    )
    telemetry.report_after_commit(
        database_alias,
        "workflow.recovery",
        metric_name="WorkflowRecoveries",
        workflow_id=str(run.pk),
        step_key=node.key,
    )
    return RetryOutcome(str(run.pk), node.key, replacement_id, False)


@scoped(transaction.atomic, using="database_alias")
def cancel_workflow_locked(
    workflow_id,
    request_id,
    reason,
    actor_reference,
    database_alias,
    backend_alias,
    actor_kind,
    origin,
    dry_run,
    intent,
):
    run = get_recovery_run(workflow_id, backend_alias, database_alias)
    adapter = get_task_adapter(run.backend_alias)
    require_same_database(adapter, database_alias)
    previous = get_previous_cancel(run, request_id, database_alias, intent)
    if previous is not None:
        return previous
    validate_cancel(run)
    if dry_run:
        return CancelOutcome(str(run.pk), False, False, dry_run=True)
    before = run.status
    settled = apply_cancel(run, adapter, database_alias)
    record_cancel(
        run,
        request_id,
        reason,
        actor_reference,
        actor_kind,
        origin,
        database_alias,
        intent,
        before,
    )
    telemetry.report_after_commit(
        database_alias,
        "workflow.cancelled",
        metric_name="WorkflowCancellations",
        workflow_id=str(run.pk),
    )
    return CancelOutcome(str(run.pk), settled, False)
