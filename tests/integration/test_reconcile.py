"""Reconciliation: expired leases, delivery holds, and due dispatch."""

from datetime import timedelta

import pytest
from django.utils import timezone

from django_tasks_aws import policies
from django_tasks_aws.models import AttemptStatus, HoldReason, TaskRecord, TaskStatus
from django_tasks_aws.services import claims, reconcile
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def catalog(overrides=None) -> policies.PolicyCatalog:
    return policies.build_policy_catalog(
        max_attempts=5,
        allowed_routes=("short", "documents"),
        overrides=overrides,
    )


def expire_claim(record) -> None:
    claims.claim_task(
        task_id=record.id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    TaskRecord.objects.using("postgres").filter(pk=record.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )


def test_recover_expired_attempt_holds_when_not_repeat_safe():
    record = make_task_record()
    expire_claim(record)

    recovered = reconcile.recover_expired_attempts(
        database_alias="postgres", policy_catalog=catalog(), limit=10
    )

    assert recovered == 1
    record.refresh_from_db()
    assert record.status == TaskStatus.PENDING
    assert record.held_at is not None
    assert record.attempts.get().status == AttemptStatus.ABANDONED


def test_recover_expired_attempt_retries_when_repeat_safe():
    record = make_task_record()
    expire_claim(record)

    reconcile.recover_expired_attempts(
        database_alias="postgres",
        policy_catalog=catalog(overrides={"tests.demo_tasks.add": {"ambiguous_retry": True}}),
        limit=10,
    )

    record.refresh_from_db()
    assert record.status == TaskStatus.PENDING
    assert record.held_at is None
    assert record.available_at > timezone.now()


def test_recover_expired_attempt_fails_when_budget_exhausted():
    record = make_task_record(max_attempts=1)
    expire_claim(record)

    reconcile.recover_expired_attempts(
        database_alias="postgres", policy_catalog=catalog(), limit=10
    )

    record.refresh_from_db()
    assert record.status == TaskStatus.FAILED


def test_dispatch_due_publishes_pending_work(fake_transport, manifest):
    record = make_task_record()
    dispatched, failed = reconcile.dispatch_due(
        database_alias="postgres", routes=manifest, transport=fake_transport, per_queue_limit=10
    )

    assert dispatched == 1
    assert failed == 0
    record.refresh_from_db()
    assert record.last_dispatched_at is not None


def test_hold_delivery_exhausted(manifest):
    record = make_task_record(dispatches_without_claim=12)
    held = reconcile.hold_delivery_exhausted(
        database_alias="postgres", routes=manifest, per_queue_limit=10
    )

    assert held == 1
    record.refresh_from_db()
    assert record.held_at is not None
    assert record.hold_reason == HoldReason.DELIVERY_EXHAUSTED
