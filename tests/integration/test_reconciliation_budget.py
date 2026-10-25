"""Synthetic backlog verifies bounded fair selection, without claiming production capacity."""

import json
from collections import Counter
from uuid import uuid4

import pytest

from django_tasks_aws.models import TaskRecord
from django_tasks_aws.services import reconcile
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_backlog_larger_than_sweep_budget_progresses_on_both_routes(test_backend, fake_transport):
    prototype = make_task_record()
    records = []
    for index in range(1000):
        data = {
            field.attname: getattr(prototype, field.attname)
            for field in TaskRecord._meta.concrete_fields
        }
        records.append(
            TaskRecord(
                **{**data, "id": uuid4(), "queue_name": "short" if index % 2 else "documents"}
            )
        )
    TaskRecord.objects.using("postgres").bulk_create(records)
    for _ in range(3):
        report = reconcile.run_reconciliation(
            database_alias="postgres",
            routes=test_backend.routes,
            policy_catalog=test_backend.policies,
            transport=fake_transport,
            per_queue_limit=7,
        )
        assert report.dispatched == 14 and report.dispatch_failed == 0
    sent_ids = [json.loads(body)["task_id"] for body in fake_transport.sent]
    rows = TaskRecord.objects.using("postgres").filter(pk__in=sent_ids)
    assert len(set(sent_ids)) == 42
    assert Counter(rows.values_list("queue_name", flat=True)) == {"short": 21, "documents": 21}
    assert all(row.attempts_started == 0 and row.dispatches_without_claim == 1 for row in rows)
    assert TaskRecord.objects.using("postgres").filter(last_dispatched_at=None).count() == 959


def test_deadline_interrupts_dispatch_between_independent_publications(
    test_backend, fake_transport, monkeypatch
):
    for queue in ("short", "documents"):
        for _ in range(5):
            make_task_record(queue_name=queue)
    elapsed = [0.0]
    original = fake_transport.send_message

    def send(queue, body):
        elapsed[0] += 1
        return original(queue, body)

    monkeypatch.setattr(fake_transport, "send_message", send)
    monkeypatch.setattr(reconcile.time, "monotonic", lambda: elapsed[0])
    result = reconcile.dispatch_due(
        database_alias="postgres",
        routes=test_backend.routes,
        transport=fake_transport,
        per_queue_limit=5,
        deadline=3,
    )
    assert result == (3, 0)
    sent_ids = [json.loads(body)["task_id"] for body in fake_transport.sent]
    assert set(
        TaskRecord.objects.using("postgres")
        .filter(pk__in=sent_ids)
        .values_list("queue_name", flat=True)
    ) == {"short", "documents"}
