"""Task reporting built on Powertools logging, metrics, tracing, and masking.

Application rules restrict event fields, sizes, and metric dimensions. Powertools
owns JSON output, EMF serialization, Lambda context, and correlation storage.
"""

import json
import os
import re
from contextlib import contextmanager
from urllib.parse import quote

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.metrics import single_metric
from aws_lambda_powertools.utilities.data_masking import DataMasking
from django.conf import settings

event_logger = Logger(
    service="django-tasks-aws",
    log_uncaught_exceptions=False,
)
data_masker = DataMasking()
tracer = Tracer()

ALLOWED_FIELDS = frozenset(
    {
        "task_name",
        "attempt_number",
        "dispatch_token",
        "next_available_at",
        "next_dispatch_at",
        "environment",
        "package_version",
        "application_version",
        "invariant_violations",
        "task_id",
        "attempt_id",
        "request_id",
        "message_id",
        "queue",
        "backend",
        "worker",
        "outcome",
        "error_type",
        "hold_reason",
        "producer_version",
        "worker_version",
        "function_version",
        "lambda_request_id",
        "log_group",
        "log_stream",
        "aws_region",
        "action",
        "repeated",
        "recovered",
        "held",
        "finished",
        "dispatched",
        "dispatch_failed",
        "workflow_id",
        "step_key",
        "run_status",
        "step_status",
        "waiting_reason",
    }
)
METRIC_UNITS = {
    "Heartbeat": "Count",
    "InvariantViolations": "Count",
    "DispatchFailures": "Count",
    "DispatchAccepted": "Count",
    "AttemptsStarted": "Count",
    "Successful": "Count",
    "Failed": "Count",
    "Retried": "Count",
    "Held": "Count",
    "Abandoned": "Count",
    "ExpiredLeases": "Count",
    "OldestDueAge": "Seconds",
    "OldestHoldAge": "Seconds",
    "HeldBacklog": "Count",
    "ControlActions": "Count",
    "WorkflowRecoveries": "Count",
    "WorkflowCancellations": "Count",
    "WorkflowUnfinished": "Count",
    "WorkflowVisited": "Count",
    "WorkflowDeferred": "Count",
    "WorkflowStalled": "Count",
    "WorkflowAdvanceDelay": "Seconds",
    "WorkflowReconcileErrors": "Count",
    "WorkflowCoordinatorHeartbeat": "Count",
}

MAX_EVENT_BYTES = 4096


def log_event(name: str, **fields):
    """Emit a redacted, bounded structured event through Powertools."""
    from django_tasks_aws import __version__

    fields = {
        "environment": getattr(settings, "DJANGO_TASKS_AWS_ENVIRONMENT", "default"),
        "package_version": __version__,
        "application_version": os.environ.get("TASK_APPLICATION_VERSION", __version__),
        **event_logger.thread_safe_get_current_keys(),
        **fields,
    }
    record = {"event": name[:80]}
    record.update(
        {
            key: str(value)[:256]
            for key, value in fields.items()
            if key in ALLOWED_FIELDS and value is not None
        }
    )
    bound(record)
    event_logger.info(record)


def bound(record: dict) -> None:
    """Truncate the largest field until the record fits the byte budget."""
    while len(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) > (
        MAX_EVENT_BYTES
    ):
        longest = max(record, key=lambda key: len(record[key]))
        record[longest] = record[longest][: len(record[longest]) // 2]


def emit_metric(name: str, value: float = 1, *, queue: str | None = None):
    """Emit one bounded-dimension EMF metric as a raw JSON line."""
    if name not in METRIC_UNITS:
        raise ValueError("Unknown task metric")
    environment = getattr(settings, "DJANGO_TASKS_AWS_ENVIRONMENT", "default")
    dimensions = {"Environment": str(environment)[:64]}
    if queue is not None:
        dimensions["Queue"] = queue[:80]
    with single_metric(
        name=name,
        unit=METRIC_UNITS[name],
        value=value,
        namespace="DjangoTasksAws",
        default_dimensions=dimensions,
    ):
        pass


def build_cloudwatch_url(region: str, log_group: str, log_stream: str) -> str | None:
    if not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", region or ""):
        return None
    if not log_group or not log_stream or len(log_group) > 512 or len(log_stream) > 512:
        return None
    if any(ord(character) < 32 for character in log_group + log_stream):
        return None

    def encode(value):
        return quote(quote(value, safe=""), safe="").replace("%", "$")

    domain = "amazonaws.cn" if region.startswith("cn-") else "amazonaws.com"
    return (
        f"https://{region}.console.{domain}/cloudwatch/home?region={region}"
        f"#logsV2:log-groups/log-group/{encode(log_group)}/log-events/{encode(log_stream)}"
    )


_INSIGHTS_QUERY = (
    "fields @timestamp, event, task_id, attempt_id, "
    "function_request_id, lambda_request_id, error_type\n"
    '| filter task_id = "{task_id}"\n'
    "| sort @timestamp asc\n"
    "| limit 200"
)


def build_logs_insights_url(region: str, log_group: str, task_id: str) -> str | None:
    """Build a CloudWatch Logs Insights deep link filtered to one task's events."""
    if not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", region or ""):
        return None
    if not log_group or not task_id or len(log_group) > 512 or len(task_id) > 128:
        return None
    if any(ord(character) < 32 for character in log_group + task_id):
        return None

    query = _INSIGHTS_QUERY.format(task_id=task_id)
    detail = (
        "~(end~0~start~-3600~timeType~'RELATIVE~tz~'UTC~unit~'seconds"
        f"~editorString~'{encode_jsurl(query)}~source~(~'{encode_jsurl(log_group)}))"
    )
    domain = "amazonaws.cn" if region.startswith("cn-") else "amazonaws.com"
    return (
        f"https://{region}.console.{domain}/cloudwatch/home?region={region}"
        f"#logsV2:logs-insights$3FqueryDetail$3D{detail}"
    )


def encode_jsurl(value: str) -> str:
    """Encode a string for the CloudWatch console's jsurl queryDetail format."""
    encoded = []
    for character in value:
        if character.isascii() and (character.isalnum() or character in "_-."):
            encoded.append(character)
        else:
            encoded.extend(f"*{byte:02x}" for byte in character.encode("utf-8"))
    return "".join(encoded)


def get_release_metadata(context):
    from django_tasks_aws import __version__

    return {
        "aws_region": os.environ.get("AWS_REGION", ""),
        "worker_version": os.environ.get("TASK_APPLICATION_VERSION", __version__),
        "function_version": getattr(context, "function_version", "")
        or os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", ""),
    }


def report_after_commit(database_alias, name, *, metric_name=None, queue=None, **fields):
    from django.db import transaction

    def report():
        log_event(name, queue=queue, **fields)
        if metric_name is not None:
            emit_metric(metric_name, queue=queue)

    transaction.on_commit(report, using=database_alias, robust=True)


def report_finalization(task_id, attempt_id, database_alias, *, queue, outcome, **fields):
    report_after_commit(
        database_alias,
        "attempt.finalized" if attempt_id is not None else "task.held",
        metric_name=outcome,
        queue=queue,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome=outcome,
        **get_workflow_fields(task_id, database_alias),
        **fields,
    )


def get_workflow_fields(task_id, database_alias):
    from django.apps import apps

    if not apps.is_installed("django_tasks_aws.workflows"):
        return {}
    from django_tasks_aws.workflows.models import WorkflowNode

    node = (
        WorkflowNode.objects.using(database_alias)
        .filter(task_id=task_id)
        .values("run_id", "key")
        .first()
    )
    return {"workflow_id": str(node["run_id"]), "step_key": node["key"]} if node else {}


def report_sweep_health(report, database_alias, routes, *, backend_alias="default"):
    from dataclasses import asdict

    from django.db.models import Count, Min, Q

    from django_tasks_aws.models import TaskRecord
    from django_tasks_aws.services.inspection import get_database_time

    now = get_database_time(database_alias)
    health = (
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
    log_event("sweep.completed", **asdict(report))
    emit_metric("InvariantViolations", report.invariant_violations)
    emit_metric("Heartbeat")
    emit_metric("ExpiredLeases", health["expired"])
    emit_metric("HeldBacklog", health["holds"])
    emit_metric(
        "OldestHoldAge",
        max(0, (now - health["oldest_hold"]).total_seconds()) if health["oldest_hold"] else 0,
    )
    emit_metric("OldestDueAge", report.oldest_due_age_seconds or 0)


CREDENTIAL_MASKS = (
    (r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]"),
    (r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@"),
    (
        r"(?i)\b(password|passwd|secret|api[-_]?key|access[-_]?token|refresh[-_]?token)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
    ),
)


def redact_text(value: str, limit=2000):
    for pattern, replacement in CREDENTIAL_MASKS:
        value = data_masker.erase(value, regex_pattern=pattern, mask_format=replacement)
    return value[:limit]


def sanitize_error(error):
    return {
        "error_type": redact_text(str(error.get("error_type", "builtins.Exception")), 256),
        "message": redact_text(str(error.get("message", ""))),
        "uncertain": bool(error.get("uncertain", True)),
    }


@contextmanager
def bind_context(**fields):
    """Restore outer task context, including after a nested task or an exception."""
    previous = event_logger.thread_safe_get_current_keys().copy()
    event_logger.thread_safe_append_keys(**fields)
    try:
        yield
    finally:
        event_logger.thread_safe_remove_keys(fields)
        event_logger.thread_safe_append_keys(**previous)


def bind_task_context(record, claimed, database_alias):
    return bind_context(
        task_name=record.task_name,
        backend=record.backend_alias,
        attempt_number=claimed.attempt_number,
        producer_version=record.producer_version,
        **get_workflow_fields(record.pk, database_alias),
    )
