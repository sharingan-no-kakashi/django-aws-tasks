"""Import-path resolution and validation."""

from types import SimpleNamespace

import pytest
from django.tasks import Task

from django_tasks_aws.exceptions import TaskResolutionError
from django_tasks_aws.import_paths import (
    reconstruct_record_task,
    resolve_task,
)


def test_resolve_task_returns_task() -> None:
    resolved = resolve_task("tests.demo_tasks.add")
    assert isinstance(resolved, Task)
    assert resolved.queue_name == "short"


def test_resolve_task_rejects_non_task_object() -> None:
    with pytest.raises(TaskResolutionError):
        resolve_task("django_tasks_aws.models.TaskStatus")


def test_resolve_task_rejects_missing_module() -> None:
    with pytest.raises(TaskResolutionError):
        resolve_task("no.such.module.task")


def test_allowlist_rejects_disallowed_prefix() -> None:
    with pytest.raises(TaskResolutionError):
        resolve_task("tests.demo_tasks.add", allowed_modules=("sample_app.tasks",))


def test_allowlist_accepts_allowed_prefix() -> None:
    resolved = resolve_task("tests.demo_tasks.add", allowed_modules=("tests.demo_tasks",))
    assert isinstance(resolved, Task)


def test_reconstruct_record_task_does_not_mutate_global() -> None:
    original = resolve_task("tests.demo_tasks.add")
    record = SimpleNamespace(backend_alias="default", queue_name="documents")
    bound = reconstruct_record_task(record, task=original)

    assert bound.queue_name == "documents"
    assert original.queue_name == "short"
