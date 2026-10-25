"""Workflow-owned state, separate from task execution and lease bookkeeping."""

from uuid import uuid4

from django.db import models
from django.db.models import Q

from django_tasks_aws.databases.constraints import SubmissionKeyConstraint
from django_tasks_aws.databases.fields import IdentifierField


class RunStatus(models.TextChoices):
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeStatus(models.TextChoices):
    WAITING = "WAITING"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class WorkflowActionType(models.TextChoices):
    RETRY = "retry"
    CANCEL = "cancel"


class WorkflowRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    backend_alias = IdentifierField(max_length=255)
    submission_key = IdentifierField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=200, blank=True, default="")
    business_reference = IdentifierField(max_length=255, blank=True, default="")
    definition_hash = models.CharField(max_length=64)
    definition_version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=RunStatus, default=RunStatus.RUNNING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_reconciled_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    cancel_requested_at = models.DateTimeField(null=True)
    details_expired_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            SubmissionKeyConstraint(
                fields=["backend_alias", "submission_key"], name="workflow_submission_unique"
            ),
            models.CheckConstraint(
                condition=Q(status__in=RunStatus.values), name="workflow_run_status_valid"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status__in=["SUCCESSFUL", "FAILED", "CANCELLED"], finished_at__isnull=False)
                    | Q(status__in=["RUNNING", "BLOCKED"], finished_at__isnull=True)
                ),
                name="workflow_run_finished_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["backend_alias", "status", "last_reconciled_at"]),
            models.Index(fields=["business_reference"], name="workflow_business_reference"),
        ]


class WorkflowNode(models.Model):
    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="nodes")
    key = models.CharField(max_length=32)
    position = models.PositiveIntegerField()
    name = models.CharField(max_length=200, blank=True, default="")
    task_config = models.JSONField()
    arguments = models.JSONField()
    task_id = models.UUIDField(null=True, unique=True)
    status = models.CharField(max_length=16, choices=NodeStatus, default=NodeStatus.WAITING)
    error = models.JSONField(default=dict)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["run", "key"], name="workflow_node_key_unique"),
            models.UniqueConstraint(
                fields=["run", "position"], name="workflow_node_position_unique"
            ),
            models.CheckConstraint(
                condition=Q(status__in=NodeStatus.values), name="workflow_node_status_valid"
            ),
        ]


class WorkflowDependency(models.Model):
    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="dependencies")
    parent = models.ForeignKey(WorkflowNode, on_delete=models.CASCADE, related_name="outgoing")
    child = models.ForeignKey(WorkflowNode, on_delete=models.CASCADE, related_name="incoming")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["parent", "child"], name="workflow_edge_unique"),
            models.CheckConstraint(condition=~Q(parent=models.F("child")), name="workflow_no_self"),
        ]


class WorkflowAction(models.Model):
    """An audited, idempotent operator action on a workflow (retry, cancel)."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=16, choices=WorkflowActionType)
    request_id = IdentifierField(max_length=200)
    intent_hash = models.TextField(default="")
    target_node = models.ForeignKey(
        WorkflowNode, on_delete=models.CASCADE, null=True, blank=True, related_name="actions"
    )
    original_task_id = models.UUIDField(null=True, blank=True)
    replacement_task_id = models.UUIDField(null=True, blank=True)
    actor_kind = models.TextField(blank=True, default="")
    actor_reference = models.TextField(blank=True, default="")
    origin = models.TextField()
    reason = models.TextField()
    before_summary = models.JSONField(default=dict, blank=True)
    after_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["request_id", "run"], name="workflow_action_request_unique"
            ),
            models.CheckConstraint(
                condition=Q(action__in=WorkflowActionType.values),
                name="workflow_action_type_valid",
            ),
        ]
