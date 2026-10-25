"""Read-only workflow status and named-output access.

These views assemble progress, dependencies, blocking reasons, and outputs into
bounded snapshots suitable for polling and web views. Default summaries omit
task arguments and results; named output access is explicit and separate.
"""

from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError

from .adapter import get_task_adapter
from .exceptions import WorkflowError, WorkflowNotFound
from .models import NodeStatus, WorkflowRun


@dataclass(frozen=True)
class NodeSnapshot:
    key: str
    name: str
    status: str
    task_id: str | None
    parents: tuple[str, ...]
    blocking_reason: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class RunSnapshot:
    id: str
    name: str
    business_reference: str
    status: str
    details_expired: bool
    created_at: datetime
    finished_at: datetime | None
    last_reconciled_at: datetime | None
    nodes: tuple[NodeSnapshot, ...]


@dataclass(frozen=True)
class RunSummary:
    id: str
    name: str
    business_reference: str
    status: str
    created_at: datetime
    finished_at: datetime | None


def list_runs(
    *,
    database_alias=None,
    backend_alias="default",
    status=None,
    name=None,
    business_reference=None,
    limit=50,
    offset=0,
):
    """Return a bounded, newest-first summary of workflow runs."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise WorkflowError("limit must be an integer between 1 and 1000")
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise WorkflowError("offset must be an integer between 0 and 100000")
    database_alias = get_database_alias(backend_alias, database_alias)
    records = WorkflowRun.objects.using(database_alias).filter(backend_alias=backend_alias)
    if status:
        records = records.filter(status=status)
    if name:
        records = records.filter(name__icontains=name)
    if business_reference:
        records = records.filter(business_reference__icontains=business_reference)
    records = records.order_by("-created_at", "-id")[offset : offset + limit]
    return tuple(
        RunSummary(
            id=str(run.pk),
            name=run.name,
            business_reference=run.business_reference,
            status=run.status,
            created_at=run.created_at,
            finished_at=run.finished_at,
        )
        for run in records
    )


def get_run_status(workflow_id, *, database_alias=None, backend_alias="default"):
    """Return a bounded snapshot of a workflow's progress and blocking reasons."""
    database_alias = get_database_alias(backend_alias, database_alias)
    run = find_workflow_run(workflow_id, backend_alias, database_alias)
    nodes = list(run.nodes.using(database_alias).all())
    status_by_key = {node.key: node.status for node in nodes}
    label_by_key = {node.key: node.name or node.key for node in nodes}
    parents = get_workflow_parents(run, nodes, database_alias)
    return RunSnapshot(
        id=str(run.pk),
        name=run.name,
        business_reference=run.business_reference,
        status=run.status,
        details_expired=run.details_expired_at is not None,
        created_at=run.created_at,
        finished_at=run.finished_at,
        last_reconciled_at=run.last_reconciled_at,
        nodes=tuple(
            build_node_snapshot(node, parents, status_by_key, label_by_key) for node in nodes
        ),
    )


def get_node_output(workflow_id, node_key, *, database_alias=None, backend_alias="default"):
    """Return the stored result of a named successful step."""
    adapter = get_task_adapter(backend_alias)
    alias = adapter.database_alias if database_alias is None else database_alias
    if alias != adapter.database_alias:
        raise WorkflowError("Selected database differs from workflow backend database")
    run = find_workflow_run(workflow_id, backend_alias, alias)
    if run.details_expired_at is not None:
        raise WorkflowError("Workflow output details have expired")
    node = find_output_node(run, node_key, alias)
    if node.status != NodeStatus.SUCCESSFUL:
        raise WorkflowError(f"Step {node_key!r} has not succeeded")
    if node.task_id is None or not adapter.is_result_available(node.task_id):
        raise WorkflowError("Step output details have expired")
    snapshot = adapter.get_snapshots([node.task_id]).get(str(node.task_id))
    if snapshot is None:
        raise WorkflowError("Step output details are unavailable")
    return snapshot.result


def get_database_alias(backend_alias, alias):
    configured = get_task_adapter(backend_alias).database_alias
    if alias is not None and alias != configured:
        raise WorkflowError("Selected database differs from workflow backend database")
    return configured


def find_output_node(run, key, alias):
    nodes = run.nodes.using(alias)
    node = nodes.filter(key=key).first()
    if node is not None:
        return node
    matches = list(nodes.filter(name=key)[:2])
    if len(matches) != 1:
        raise WorkflowError(f"Step name {key!r} is missing or ambiguous; use its key")
    return matches[0]


def find_workflow_run(workflow_id, backend_alias, alias):
    try:
        return WorkflowRun.objects.using(alias).get(pk=workflow_id, backend_alias=backend_alias)
    except (WorkflowRun.DoesNotExist, ValidationError) as error:
        raise WorkflowNotFound(str(workflow_id)) from error


def get_workflow_parents(run, nodes, alias):
    by_id = {node.pk: node for node in nodes}
    parents: dict[str, list[str]] = {node.key: [] for node in nodes}
    for parent_id, child_id in run.dependencies.using(alias).values_list("parent_id", "child_id"):
        parents[by_id[child_id].key].append(by_id[parent_id].key)
    return parents


def build_node_snapshot(node, parents, status_by_key, label_by_key):
    blocking, error = get_blocking_nodes(node, parents, status_by_key, label_by_key)
    return NodeSnapshot(
        key=node.key,
        name=node.name,
        status=node.status,
        task_id=str(node.task_id) if node.task_id is not None else None,
        parents=tuple(parents[node.key]),
        blocking_reason=blocking,
        error_type=error,
    )


def get_blocking_nodes(node, parents, status_by_key, label_by_key):
    match node.status:
        case NodeStatus.WAITING:
            pending = [
                label_by_key[key]
                for key in parents[node.key]
                if status_by_key[key] != NodeStatus.SUCCESSFUL
            ]
            return (f"waiting for {', '.join(pending)}" if pending else None), None
        case NodeStatus.BLOCKED:
            return "task held", None
        case NodeStatus.FAILED:
            return "failed", node.error.get("error_type")
        case NodeStatus.SKIPPED:
            if node.error.get("error_type") == "Cancelled":
                return "cancelled", "Cancelled"
            return "ancestor failed", node.error.get("error_type")
        case _:
            return None, None
