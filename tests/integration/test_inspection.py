from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from django_tasks_aws.services import claims, inspection
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


@pytest.mark.parametrize(
    "category,overrides",
    [
        ("due", {}),
        (
            "overdue",
            {
                "available_at": timezone.now() - timedelta(minutes=10),
                "next_dispatch_at": timezone.now() - timedelta(minutes=10),
            },
        ),
        ("scheduled", {"available_at": timezone.now() + timedelta(hours=1)}),
        ("retrying", {"available_at": timezone.now() + timedelta(hours=1), "attempts_started": 1}),
        (
            "reserved",
            {
                "dispatch_token": uuid4(),
                "dispatch_reserved_until": timezone.now() + timedelta(minutes=1),
            },
        ),
        ("held", {"held_at": timezone.now(), "hold_reason": "delivery_exhausted"}),
        (
            "running",
            {
                "status": "RUNNING",
                "lease_token": uuid4(),
                "lease_expires_at": timezone.now() + timedelta(minutes=1),
            },
        ),
        (
            "expired",
            {
                "status": "RUNNING",
                "lease_token": uuid4(),
                "lease_expires_at": timezone.now() - timedelta(minutes=1),
            },
        ),
        ("successful", {"status": "SUCCESSFUL", "finished_at": timezone.now()}),
        ("failed", {"status": "FAILED", "finished_at": timezone.now()}),
        ("exhausted", {"attempts_started": 5}),
    ],
)
def test_diagnostic_categories(test_backend, category, overrides):
    record = make_task_record(**overrides)
    page = inspection.list_tasks(test_backend, category=category)
    assert page.tasks[0]["id"] == record.id
    assert page.tasks[0]["category"] == category


def test_list_is_bounded_and_omits_payload(test_backend):
    for _ in range(4):
        make_task_record()
    with CaptureQueriesContext(connections["postgres"]) as captured:
        first = inspection.list_tasks(test_backend, limit=2)
    assert len(captured) == 2
    assert all('"payload"' not in query["sql"] for query in captured)
    assert first.next_offset == 2
    second = inspection.list_tasks(test_backend, offset=2, limit=2)
    assert not {row["id"] for row in first.tasks} & {row["id"] for row in second.tasks}
    assert second.next_offset is None


def test_problem_filters_exclude_future_retries(test_backend):
    make_task_record(available_at=timezone.now() + timedelta(minutes=1), attempts_started=1)
    make_task_record()  # Newly runnable work is not an overdue problem.
    due = make_task_record(
        available_at=timezone.now() - timedelta(minutes=10),
        next_dispatch_at=timezone.now() - timedelta(minutes=10),
    )
    assert [row["id"] for row in inspection.list_tasks(test_backend, problematic=True).tasks] == [
        due.id
    ]


def test_search_finds_attempt_and_operation_identity(test_backend):
    record = make_task_record(operation_key="order:42")
    claimed = claims.claim_task(
        task_id=str(record.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    for query in (str(claimed.attempt_id), "order:42", str(record.id)):
        assert inspection.list_tasks(test_backend, search=query).tasks[0]["id"] == record.id


def test_detail_hides_sensitive_payload_and_error_messages(test_backend):
    record = make_task_record(last_error={"error_type": "builtins.ValueError", "message": "secret"})
    detail = inspection.get_task_detail(test_backend, str(record.id))
    assert "payload" not in detail and "message" not in detail["last_error"]
    assert (
        inspection.get_task_detail(test_backend, str(record.id), sensitive=True)["payload"]
        == record.payload
    )


def test_route_summary_counts_are_separate(test_backend):
    make_task_record(queue_name="documents")
    summaries = {row["name"]: row for row in inspection.get_worker_summaries(test_backend)}
    assert summaries["documents"]["counts"]["due"] == 1
    assert summaries["short"]["counts"]["due"] == 0


def test_active_inspection_uses_index_as_terminal_history_grows(test_backend):
    import json
    import os
    from pathlib import Path

    from django_tasks_aws.models import TaskRecord, TaskStatus

    now = timezone.now()
    terminal = dict(
        task_name="tests.demo_tasks.add",
        backend_alias="default",
        payload_version=1,
        payload={},
        payload_hash="hash",
        queue_name="short",
        status=TaskStatus.SUCCESSFUL,
        available_at=now,
        next_dispatch_at=now,
        max_attempts=5,
        dedupe_scope="default",
        producer_version="test",
        finished_at=now,
    )
    TaskRecord.objects.using("postgres").bulk_create(
        [TaskRecord(**terminal) for _ in range(10000)], batch_size=500
    )
    make_task_record()
    with connections["postgres"].cursor() as cursor:
        cursor.execute("ANALYZE task_record")
    query = (
        inspection.get_annotated_tasks(test_backend)
        .filter(status=TaskStatus.PENDING)
        .order_by("created_at", "id")
        .values("id", "created_at")[:50]
    )
    plan = query.explain(format="JSON", analyze=True, buffers=True)
    assert "task_record_status_created" in plan
    if path := os.environ.get("QUERY_PLAN_REPORT"):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(json.loads(plan), indent=2))
