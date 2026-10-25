"""Durable task, attempt, and control-event records.

The SQL database owns the logical state of every accepted task. These models and
their constraints encode the invariants from the design (sections 4–5): one
current claim, both-or-neither lease/hold/dispatch fields, terminal timestamps,
bounded counters, and conditional submission-key uniqueness.
"""

from uuid import uuid4

from django.db import models
from django.db.models import F, Q

from django_tasks_aws.databases.constraints import SubmissionKeyConstraint
from django_tasks_aws.databases.fields import IdentifierField


class TaskStatus(models.TextChoices):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"


class AttemptStatus(models.TextChoices):
    RUNNING = "RUNNING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class AttemptOutcome(models.TextChoices):
    UNKNOWN = "UNKNOWN"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"


class HoldReason(models.TextChoices):
    DELIVERY_EXHAUSTED = "delivery_exhausted"
    AMBIGUOUS_OUTCOME = "ambiguous_external_outcome"
    INCOMPATIBLE_PAYLOAD = "incompatible_payload"
    CANCELLED = "cancelled"


class ControlAction(models.TextChoices):
    INVARIANT_REVIEW = "invariant_review"
    REPLAY = "replay"
    RELEASE = "release"
    DISPATCH = "dispatch"
    RECONCILE = "reconcile"


class TaskRecord(models.Model):
    """A durable task and its execution/delivery bookkeeping."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    task_name = IdentifierField(max_length=512)
    backend_alias = IdentifierField(max_length=255)
    payload_version = models.PositiveIntegerField()
    payload = models.JSONField()
    payload_hash = models.TextField()
    submission_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    task_options = models.JSONField(default=dict, blank=True, editable=False)
    queue_name = IdentifierField(max_length=80)

    status = IdentifierField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.PENDING)
    available_at = models.DateTimeField()
    next_dispatch_at = models.DateTimeField()

    dispatch_token = models.UUIDField(null=True, blank=True)
    dispatch_reserved_until = models.DateTimeField(null=True, blank=True)
    last_dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatches_without_claim = models.PositiveIntegerField(default=0)

    attempts_started = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField()

    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    held_at = models.DateTimeField(null=True, blank=True)
    hold_reason = IdentifierField(max_length=64, choices=HoldReason.choices, null=True, blank=True)

    dedupe_scope = IdentifierField(max_length=255)
    dedupe_key = IdentifierField(max_length=255, null=True, blank=True)
    operation_key = IdentifierField(max_length=255, null=True, blank=True)

    details_expired_at = models.DateTimeField(null=True, blank=True)
    diagnostics_expired_at = models.DateTimeField(null=True, blank=True)

    result = models.JSONField(null=True, blank=True)
    last_error = models.JSONField(null=True, blank=True)

    producer_version = models.TextField()
    replay_of_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    first_started_at = models.DateTimeField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "task_record"
        permissions = [
            ("control_taskrecord", "Can request audited task recovery"),
            ("view_task_payload", "Can view task arguments and results"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(payload_version__gte=1),
                name="task_record_payload_version_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1),
                name="task_record_max_attempts_positive",
            ),
            models.CheckConstraint(
                condition=Q(attempts_started__lte=F("max_attempts")),
                name="task_record_attempts_within_budget",
            ),
            models.CheckConstraint(
                condition=Q(dispatches_without_claim__gte=0),
                name="task_record_dispatches_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=TaskStatus.RUNNING,
                        lease_token__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | Q(
                        ~Q(status=TaskStatus.RUNNING),
                        lease_token__isnull=True,
                        lease_expires_at__isnull=True,
                    )
                ),
                name="task_record_lease_fields_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(held_at__isnull=True, hold_reason__isnull=True)
                    | Q(status=TaskStatus.PENDING, held_at__isnull=False, hold_reason__isnull=False)
                ),
                name="task_record_hold_fields_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(dispatch_token__isnull=True, dispatch_reserved_until__isnull=True)
                    | Q(dispatch_token__isnull=False, dispatch_reserved_until__isnull=False)
                ),
                name="task_record_dispatch_fields_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=[TaskStatus.SUCCESSFUL, TaskStatus.FAILED],
                        finished_at__isnull=False,
                    )
                    | Q(
                        status__in=[TaskStatus.PENDING, TaskStatus.RUNNING],
                        finished_at__isnull=True,
                    )
                ),
                name="task_record_finished_at_consistent",
            ),
            SubmissionKeyConstraint(
                fields=["dedupe_scope", "dedupe_key"],
                name="task_record_business_key",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at", "id"], name="task_record_status_created"),
            models.Index(
                fields=["finished_at", "id"],
                name="task_record_terminal_cleanup",
            ),
            models.Index(
                fields=["queue_name", "status", "held_at", "next_dispatch_at", "id"],
                name="task_record_due_dispatch",
            ),
            models.Index(
                fields=["status", "lease_expires_at", "id"],
                name="task_record_expired_lease",
            ),
            models.Index(fields=["operation_key"], name="task_record_operation_key"),
            models.Index(fields=["task_name"], name="task_record_task_name"),
        ]

    def __str__(self) -> str:
        return f"{self.task_name} ({self.id})"


class TaskAttempt(models.Model):
    """One claimed execution attempt; its ``id`` is also the lease token."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    task = models.ForeignKey(TaskRecord, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveIntegerField()

    status = IdentifierField(
        max_length=16, choices=AttemptStatus.choices, default=AttemptStatus.RUNNING
    )
    outcome = IdentifierField(
        max_length=32, choices=AttemptOutcome.choices, default=AttemptOutcome.UNKNOWN
    )

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    error_type = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    traceback = models.TextField(null=True, blank=True)

    lambda_request_id = models.TextField(blank=True, default="")
    log_group = models.TextField(blank=True, default="")
    log_stream = models.TextField(blank=True, default="")
    aws_region = models.TextField(blank=True, default="")

    worker_name = IdentifierField(max_length=80)
    worker_version = models.TextField(blank=True, default="")
    function_version = models.TextField(blank=True, default="")
    sqs_message_id = models.TextField(blank=True, default="")
    progress = models.JSONField(default=dict, blank=True)
    progress_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "task_attempt"
        ordering = ["attempt_number"]
        indexes = [
            models.Index(
                fields=["task", "status"],
                name="task_attempt_running_task",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=AttemptStatus.values), name="task_attempt_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(outcome__in=AttemptOutcome.values), name="task_attempt_outcome_valid"
            ),
            models.UniqueConstraint(
                fields=["task", "attempt_number"],
                name="task_attempt_number_unique",
            ),
            models.CheckConstraint(
                condition=Q(attempt_number__gte=1),
                name="task_attempt_number_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_id}#{self.attempt_number}"


class TaskControlEvent(models.Model):
    """An audited developer/operator action (release, replay, dispatch, ...)."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    request_id = IdentifierField(max_length=200)
    intent_hash = models.TextField(default="")
    action = IdentifierField(max_length=32, choices=ControlAction.choices)
    target_scope = IdentifierField(max_length=255)

    task_id = models.UUIDField(null=True, blank=True)
    replacement_task_id = models.UUIDField(null=True, blank=True)

    actor_kind = models.TextField(blank=True, default="")
    actor_reference = models.TextField(blank=True, default="")
    origin = models.TextField()
    reason = models.TextField()

    details_expired_at = models.DateTimeField(null=True, blank=True)

    before_summary = models.JSONField(default=dict, blank=True)
    after_summary = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_control_event"
        constraints = [
            models.UniqueConstraint(
                fields=["request_id", "target_scope"],
                name="task_control_request_target_unique",
            )
        ]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["request_id"], name="task_control_request_id"),
            models.Index(fields=["task_id"], name="task_control_task_id"),
        ]

    def __str__(self) -> str:
        return f"{self.action}:{self.request_id}"
