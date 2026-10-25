"""Shared test factories for durable records."""

from django.utils import timezone

from django_tasks_aws.models import TaskRecord, TaskStatus


def make_task_record(using: str = "postgres", **overrides) -> TaskRecord:
    data = {
        "task_name": "tests.demo_tasks.add",
        "backend_alias": "default",
        "payload_version": 1,
        "payload": {"args": [1, 2], "kwargs": {}},
        "payload_hash": "h",
        "queue_name": "short",
        "status": TaskStatus.PENDING,
        "available_at": timezone.now(),
        "next_dispatch_at": timezone.now(),
        "max_attempts": 5,
        "dedupe_scope": "default",
        "producer_version": "0.1.0",
    }
    data.update(overrides)
    return TaskRecord.objects.using(using).create(**data)
