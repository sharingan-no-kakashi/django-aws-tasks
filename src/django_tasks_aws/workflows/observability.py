"""Bounded coordinator signals; run identities stay in logs, never metric dimensions."""

from django.utils import timezone

from django_tasks_aws import telemetry

from .configuration import get_workflow_limits
from .models import NodeStatus


def report_advancement(run, nodes, parents, snapshots):
    ready = [
        node
        for node in nodes
        if node.status == NodeStatus.WAITING
        and all(parent.status == NodeStatus.SUCCESSFUL for parent in parents[node.key])
    ]
    completions = [get_ready_since(node, parents, snapshots, run.created_at) for node in ready]
    delay = (
        max(0, (timezone.now() - min(completions)).total_seconds()) if ready and completions else 0
    )
    stalled = bool(ready) and delay >= get_workflow_limits().stalled_seconds
    telemetry.emit_metric("WorkflowDeferred", len(ready))
    telemetry.emit_metric("WorkflowStalled", int(stalled))
    telemetry.emit_metric("WorkflowAdvanceDelay", delay)
    telemetry.log_event(
        "workflow.waiting",
        workflow_id=str(run.pk),
        waiting_reason=get_waiting_reason(run, nodes, ready, stalled),
    )


def get_ready_since(node, parents, snapshots, fallback):
    completed = [
        snapshots[str(parent.task_id)].finished_at
        for parent in parents[node.key]
        if str(parent.task_id) in snapshots
        and snapshots[str(parent.task_id)].finished_at is not None
    ]
    return max(completed) if completed else fallback


def get_waiting_reason(run, nodes, ready, stalled):
    if run.cancel_requested_at is not None:
        return "cancellation_settling"
    if stalled:
        return "coordinator_stalled"
    if ready:
        return "advancement_deferred"
    if any(node.error.get("error_type") == "PreparationError" for node in nodes):
        return "preparation_failed"
    if any(node.status == NodeStatus.BLOCKED for node in nodes):
        return "operator_held"
    if all(
        node.status in {NodeStatus.SUCCESSFUL, NodeStatus.FAILED, NodeStatus.SKIPPED}
        for node in nodes
    ):
        return "settled"
    return "healthy_waiting"
