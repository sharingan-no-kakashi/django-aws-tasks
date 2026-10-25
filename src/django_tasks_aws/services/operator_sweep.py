"""Audited per-target reconciliation for the operator command."""

from dataclasses import asdict

from django.db import DatabaseError
from django.db.models import Q

from django_tasks_aws.exceptions import DjangoTasksAWSError
from django_tasks_aws.services.control import operate
from django_tasks_aws.services.inspection import get_annotated_tasks, get_database_time


def sweep(backend, request, *, queue=None, limit=100, dry_run=False, no_dispatch=False):
    if not 1 <= limit <= 200 or (queue and queue not in backend.routes):
        raise ValueError("Select a configured route and a limit of 1–200")
    now = get_database_time(backend.database_alias)
    records = (
        get_annotated_tasks(backend)
        .filter(
            Q(status="RUNNING", lease_expires_at__lte=now)
            | Q(
                status="PENDING",
                held_at__isnull=True,
                available_at__lte=now,
                next_dispatch_at__lte=now,
            )
        )
        .exclude(queue_name__in=backend.paused_routes)
    )
    if queue:
        records = records.filter(queue_name=queue)
    records = records.only(
        "id", "queue_name", "status", "attempts_started", "max_attempts", "dispatches_without_claim"
    ).order_by("next_dispatch_at", "id")[:limit]
    results = [one(backend, record, request, dry_run, no_dispatch) for record in records]
    return {"dry_run": dry_run, "results": results}


def one(backend, record, request, dry_run, no_dispatch):
    exhausted = (
        record.attempts_started >= record.max_attempts
        or record.dispatches_without_claim >= backend.routes[record.queue_name].dispatch_limit
    )
    action = "reconcile" if record.status == "RUNNING" or exhausted else "dispatch"
    if no_dispatch and action == "dispatch":
        return {"task_id": str(record.id), "skipped": "publication disabled"}
    try:
        return asdict(
            operate(
                backend=backend,
                task_id=str(record.id),
                action=action,
                request=request,
                dry_run=dry_run,
            )
        )
    except (DjangoTasksAWSError, DatabaseError, ValueError) as error:
        return {"task_id": str(record.id), "error": str(error)}
