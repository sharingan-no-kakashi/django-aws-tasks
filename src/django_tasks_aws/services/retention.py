"""Bounded redaction that retains acceptance, deduplication, and control identities."""

from dataclasses import dataclass
from datetime import timedelta

from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.databases.locking import select_bounded_ids
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import ControlRejected
from django_tasks_aws.models import TaskControlEvent, TaskRecord, TaskStatus
from django_tasks_aws.services.inspection import get_database_time


@dataclass(frozen=True)
class RetentionReport:
    redacted: int
    task_ids: tuple[str, ...]
    dry_run: bool
    diagnostics_redacted: int = 0
    audit_redacted: int = 0


def expire_details(backend, *, limit=100, dry_run=False):
    if not 1 <= limit <= 1000:
        raise ControlRejected("Retention limit must be 1–1000")
    failure_days = backend.options.get("failure_diagnostic_retention_days", 90)
    audit_days = backend.options.get("audit_detail_retention_days", 90)
    if not all(
        isinstance(days, int) and not isinstance(days, bool) for days in (failure_days, audit_days)
    ):
        raise ControlRejected("Diagnostic retention periods must be integers")
    if min(failure_days, audit_days) < max(30, backend.result_retention.days):
        raise ControlRejected("Diagnostic and audit retention must cover the result/replay window")
    now = get_database_time(backend.database_alias)
    return redact_expired_details(backend, now, failure_days, audit_days, limit, dry_run)


def get_terminal_tasks(backend):
    return TaskRecord.objects.using(backend.database_alias).filter(
        backend_alias=backend.alias,
        status__in=[TaskStatus.SUCCESSFUL, TaskStatus.FAILED],
    )


def select_identities(records, limit, dry_run):
    from django_tasks_aws.workflows.adapter import uses_aws_workflows

    if uses_aws_workflows():
        from django_tasks_aws.workflows.aws import protect_workflow_results

        records = protect_workflow_results(sender=None, records=records)
    return select_bounded_ids(records.order_by("finished_at", "id"), limit, lock=not dry_run)


def redact(backend, identities, now):
    records = get_terminal_tasks(backend).filter(id__in=identities)
    records.update(payload={}, result=None, details_expired_at=now, updated_at=now)
    successful = records.filter(status=TaskStatus.SUCCESSFUL)
    for record in successful:
        record.attempts.all().delete()
    successful.update(last_error=None, diagnostics_expired_at=now)


def purge_diagnostics(backend, now, days, limit, dry_run):
    records = get_terminal_tasks(backend).filter(
        status=TaskStatus.FAILED,
        diagnostics_expired_at__isnull=True,
        finished_at__lt=now - timedelta(days=days),
    )
    identities = select_identities(records, limit, dry_run)
    if not dry_run:
        for record in records.filter(id__in=identities):
            record.attempts.all().delete()
        records.filter(id__in=identities).update(last_error=None, diagnostics_expired_at=now)
    return len(identities)


def purge_audits(backend, now, days, limit, dry_run):
    # Keep request/intent hashes and result identities indefinitely, so redaction
    # cannot turn an old repeated request into a second business operation.
    events = TaskControlEvent.objects.using(backend.database_alias).filter(
        details_expired_at__isnull=True,
        created_at__lt=now - timedelta(days=days),
        task_id__in=TaskRecord.objects.using(backend.database_alias)
        .filter(
            backend_alias=backend.alias,
        )
        .values("id"),
    )
    identities = select_bounded_ids(events.order_by("created_at", "id"), limit, lock=not dry_run)
    if not dry_run:
        events.filter(id__in=identities).update(
            actor_reference="[expired]", reason="[expired]", details_expired_at=now
        )
    return len(identities)


@scoped(manage_coordination_transaction, "backend.database_alias")
def redact_expired_details(backend, now, failure_days, audit_days, limit, dry_run):
    records = get_terminal_tasks(backend).filter(
        details_expired_at__isnull=True, finished_at__lt=now - backend.result_retention
    )
    identities = select_identities(records, limit, dry_run)
    diagnostics = purge_diagnostics(backend, now, failure_days, limit, dry_run)
    audits = purge_audits(backend, now, audit_days, limit, dry_run)
    if not dry_run:
        redact(backend, identities, now)
    return RetentionReport(
        len(identities), tuple(map(str, identities)), dry_run, diagnostics, audits
    )
