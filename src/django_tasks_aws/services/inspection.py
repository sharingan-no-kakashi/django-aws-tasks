"""Bounded read-only diagnostics shared by command and browser interfaces."""

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from urllib.parse import quote
from uuid import UUID

from django.db import connections
from django.db.models import Case, CharField, Count, Exists, F, Min, OuterRef, Q, Value, When
from django.utils import timezone

from django_tasks_aws.databases import get_database_adapter
from django_tasks_aws.exceptions import ControlRejected
from django_tasks_aws.models import TaskAttempt, TaskControlEvent, TaskRecord, TaskStatus


class Category(StrEnum):
    SCHEDULED = "scheduled"
    DUE = "due"
    OVERDUE = "overdue"
    RESERVED = "reserved"
    RUNNING = "running"
    EXPIRED = "expired"
    RETRYING = "retrying"
    HELD = "held"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


PROBLEM_CATEGORIES = (
    Category.FAILED,
    Category.HELD,
    Category.EXPIRED,
    Category.OVERDUE,
    Category.EXHAUSTED,
)
SUMMARY_FIELDS = (
    "id",
    "task_name",
    "backend_alias",
    "queue_name",
    "status",
    "held_at",
    "hold_reason",
    "available_at",
    "next_dispatch_at",
    "lease_expires_at",
    "dispatch_reserved_until",
    "attempts_started",
    "max_attempts",
    "dispatches_without_claim",
    "created_at",
    "finished_at",
    "payload_version",
    "operation_key",
    "replay_of_id",
    "details_expired_at",
    "category",
)


@dataclass(frozen=True)
class TaskPage:
    tasks: list[dict]
    next_offset: int | None


def get_database_time(database_alias):
    connection = connections[database_alias]
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {get_database_adapter(database_alias).clock_sql}"
            + connection.features.bare_select_suffix
        )
        now = cursor.fetchone()[0]
    if timezone.is_naive(now) and connection.timezone is not None:
        return timezone.make_aware(now, connection.timezone)
    return now


def get_annotated_tasks(backend, now=None):
    now = now or get_database_time(backend.database_alias)
    category = Case(
        When(status=TaskStatus.FAILED, then=Value(Category.FAILED)),
        When(status=TaskStatus.SUCCESSFUL, then=Value(Category.SUCCESSFUL)),
        When(held_at__isnull=False, then=Value(Category.HELD)),
        When(status=TaskStatus.RUNNING, lease_expires_at__lte=now, then=Value(Category.EXPIRED)),
        When(status=TaskStatus.RUNNING, then=Value(Category.RUNNING)),
        When(attempts_started__gte=F("max_attempts"), then=Value(Category.EXHAUSTED)),
        When(available_at__gt=now, attempts_started__gt=0, then=Value(Category.RETRYING)),
        When(available_at__gt=now, then=Value(Category.SCHEDULED)),
        When(dispatch_reserved_until__gt=now, then=Value(Category.RESERVED)),
        When(
            available_at__lte=now
            - timedelta(seconds=backend.options.get("overdue_grace_seconds", 300)),
            next_dispatch_at__lte=now,
            then=Value(Category.OVERDUE),
        ),
        default=Value(Category.DUE),
        output_field=CharField(),
    )
    return (
        TaskRecord.objects.using(backend.database_alias)
        .filter(
            backend_alias=backend.alias,
        )
        .annotate(category=category)
    )


def list_tasks(
    backend,
    *,
    queue=None,
    category=None,
    problematic=False,
    search=None,
    limit=50,
    offset=0,
    status=None,
    task_name=None,
    hold_reason=None,
):
    if not 1 <= limit <= 200 or offset < 0 or offset > 100_000:
        raise ControlRejected("Limit must be 1–200 and offset must be 0–100000")
    now = get_database_time(backend.database_alias)
    records = get_annotated_tasks(backend, now)
    filters = {
        name: value
        for name, value in {
            "queue_name": queue,
            "status": status,
            "task_name": task_name,
            "hold_reason": hold_reason,
        }.items()
        if value
    }
    records = records.filter(**filters)
    if category:
        records = records.filter(build_category_filter(Category(category), backend, now))
    if problematic:
        records = records.filter(build_problem_filter(backend, now))
    if search:
        records = search_tasks(records, search)
    rows = list(
        records.order_by("created_at", "id").values(*SUMMARY_FIELDS)[offset : offset + limit + 1]
    )
    return TaskPage(
        [explain(row) for row in rows[:limit]], offset + limit if len(rows) > limit else None
    )


def build_category_filter(category, backend, now):
    pending = Q(status=TaskStatus.PENDING, held_at__isnull=True)
    remaining = pending & Q(attempts_started__lt=F("max_attempts"))
    ready = remaining & Q(available_at__lte=now)
    unreserved = Q(dispatch_reserved_until__isnull=True) | Q(dispatch_reserved_until__lte=now)
    overdue = (
        ready
        & unreserved
        & Q(
            next_dispatch_at__lte=now,
            available_at__lte=now
            - timedelta(seconds=backend.options.get("overdue_grace_seconds", 300)),
        )
    )
    predicates = {
        Category.SUCCESSFUL: Q(status=TaskStatus.SUCCESSFUL),
        Category.FAILED: Q(status=TaskStatus.FAILED),
        Category.HELD: Q(status=TaskStatus.PENDING, held_at__isnull=False),
        Category.RUNNING: Q(status=TaskStatus.RUNNING, lease_expires_at__gt=now),
        Category.EXPIRED: Q(status=TaskStatus.RUNNING, lease_expires_at__lte=now),
        Category.EXHAUSTED: pending & Q(attempts_started__gte=F("max_attempts")),
        Category.SCHEDULED: remaining & Q(available_at__gt=now, attempts_started=0),
        Category.RETRYING: remaining & Q(available_at__gt=now, attempts_started__gt=0),
        Category.RESERVED: ready & Q(dispatch_reserved_until__gt=now),
        Category.OVERDUE: overdue,
        Category.DUE: ready & unreserved & ~overdue,
    }
    return predicates[category]


def build_problem_filter(backend, now):
    predicate = Q(pk__in=[])
    for category in PROBLEM_CATEGORIES:
        predicate |= build_category_filter(category, backend, now)
    return predicate


def search_tasks(records, search):
    match = Q(task_name__icontains=search) | Q(operation_key__icontains=search)
    match |= Q(dedupe_key__icontains=search)
    try:
        identity = UUID(search)
    except ValueError:
        return records.filter(match)
    attempts = TaskAttempt.objects.filter(task_id=OuterRef("pk"), id=identity)
    return records.annotate(matching_attempt=Exists(attempts)).filter(
        match | Q(id=identity) | Q(matching_attempt=True)
    )


def get_task_detail(backend, task_id, *, sensitive=False, history_limit=100):
    if not 1 <= history_limit <= 200:
        raise ControlRejected("History limit must be 1–200")
    records = get_annotated_tasks(backend).filter(pk=UUID(str(task_id)))
    fields = [*SUMMARY_FIELDS, "last_error"]
    if sensitive:
        fields.extend(["payload", "result", "dedupe_key"])
    row = records.values(*fields).first()
    if row is None:
        raise ControlRejected(f"Task {task_id} does not exist on this backend")
    row["last_error"] = (
        row.pop("last_error") if sensitive else get_error_category(row.pop("last_error"))
    )
    row["attempts"] = inspect_attempts(backend, task_id, sensitive, history_limit)
    row["control_events"] = get_control_events(backend, task_id, history_limit)
    row["replacements"] = list(
        records.model.objects.using(backend.database_alias)
        .filter(
            replay_of_id=task_id,
            backend_alias=backend.alias,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:history_limit]
    )
    return explain(row)


def explain(row):
    explanations = {
        Category.SCHEDULED: ("Not eligible before its scheduled execution time.", []),
        Category.RETRYING: ("A previous attempt scheduled a future retry.", []),
        Category.DUE: ("Runnable; no overdue threshold has been crossed.", ["dispatch"]),
        Category.OVERDUE: (
            "Runnable work exceeded its grace period; inspect delivery and worker health.",
            ["dispatch"],
        ),
        Category.RESERVED: ("A live publication reservation suppresses another dispatch.", []),
        Category.RUNNING: ("A current attempt owns the lease; wait for completion or expiry.", []),
        Category.EXPIRED: (
            "Lease expired with an unknown outcome; recover through policy.",
            ["reconcile"],
        ),
        Category.HELD: (
            "Automatic processing is held; review the cause before release.",
            ["release"],
        ),
        Category.EXHAUSTED: (
            "Execution budget is exhausted; recover to a terminal decision.",
            ["reconcile"],
        ),
        Category.SUCCESSFUL: (
            "Confirmed successful; any replay requires effect review.",
            ["replay"],
        ),
        Category.FAILED: (
            "Automatic processing ended; effects may still have occurred.",
            ["replay"],
        ),
    }
    reason, actions = explanations[Category(row["category"])]
    return dict(row, explanation=reason, candidate_actions=actions)


def get_error_category(error):
    if not error:
        return None
    return {key: error[key] for key in ("error_type", "uncertain") if key in error}


def inspect_attempts(backend, task_id, sensitive, limit):
    fields = [
        "id",
        "attempt_number",
        "status",
        "outcome",
        "started_at",
        "finished_at",
        "lease_expires_at",
        "worker_name",
        "error_type",
        "lambda_request_id",
        "log_group",
        "log_stream",
        "aws_region",
        "worker_version",
        "function_version",
        "progress",
        "progress_at",
        "task__lease_token",
        "task__status",
    ]
    if sensitive:
        fields.extend(["error_message", "traceback"])
    rows = list(
        TaskAttempt.objects.using(backend.database_alias)
        .filter(
            task_id=task_id,
        )
        .order_by("-attempt_number")
        .values(*fields)[:limit]
    )
    now = get_database_time(backend.database_alias)
    for row in rows:
        update_progress_snapshot(row, now, sensitive)
        row["log_url"] = build_cloudwatch_url(
            row["aws_region"], row["log_group"], row["log_stream"]
        )
        row["insights_url"] = build_logs_insights_url(row["aws_region"], row["log_group"], task_id)
    return rows


def update_progress_snapshot(row, now, sensitive):
    token = row.pop("task__lease_token")
    status = row.pop("task__status")
    live = (
        status == "RUNNING"
        and token == row["id"]
        and row["lease_expires_at"] is not None
        and row["lease_expires_at"] > now
    )
    row["progress_state"] = (
        "current" if live else ("stale" if row["status"] == "RUNNING" else "previous")
    )
    if not sensitive:
        row["progress"] = {
            key: value for key, value in row["progress"].items() if key in {"completed", "total"}
        }


def get_control_events(backend, task_id, limit):
    return list(
        TaskControlEvent.objects.using(backend.database_alias)
        .filter(
            task_id=task_id,
        )
        .order_by("-created_at", "id")
        .values(
            "id",
            "request_id",
            "action",
            "actor_kind",
            "actor_reference",
            "origin",
            "reason",
            "replacement_task_id",
            "before_summary",
            "after_summary",
            "created_at",
        )[:limit]
    )


def get_worker_summaries(backend):
    from dataclasses import asdict

    groups = (
        get_annotated_tasks(backend)
        .filter(status__in=[TaskStatus.PENDING, TaskStatus.RUNNING])
        .values("queue_name", "category")
        .annotate(count=Count("id"), oldest=Min("available_at"))
    )
    groups = list(groups)
    counts = {(row["queue_name"], row["category"]): row["count"] for row in groups}
    return [
        dict(
            asdict(route),
            paused=name in backend.paused_routes,
            oldest_due_at=min(
                (
                    row["oldest"]
                    for row in groups
                    if row["queue_name"] == name
                    and row["category"] in (Category.DUE, Category.OVERDUE)
                ),
                default=None,
            ),
            counts={
                category: counts.get((name, category), 0)
                for category in Category
                if category not in (Category.SUCCESSFUL, Category.FAILED)
            },
        )
        for name, route in backend.routes.items()
    ]


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
