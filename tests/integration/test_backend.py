"""Public Django Tasks API through the PostgreSQL backend."""

import pytest
from django.tasks import TaskResultStatus
from django.tasks.exceptions import InvalidTask, TaskResultDoesNotExist

from django_tasks_aws.models import TaskRecord, TaskStatus
from tests.demo_tasks import add, render

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_enqueue_creates_pending_record(test_backend):
    result = test_backend.enqueue(add, (1, 2), {})
    assert result.status is TaskResultStatus.READY

    record = TaskRecord.objects.using("postgres").get(pk=result.id)
    assert record.status == TaskStatus.PENDING
    assert record.task_name == "tests.demo_tasks.add"
    assert record.queue_name == "short"


def test_get_result_round_trips(test_backend):
    result = test_backend.enqueue(add, (3, 4), {})
    fetched = test_backend.get_result(result.id)

    assert fetched.id == result.id
    assert fetched.args == [3, 4]
    assert fetched.status is TaskResultStatus.READY


def test_get_result_missing_raises(test_backend):
    with pytest.raises(TaskResultDoesNotExist):
        test_backend.get_result("00000000-0000-0000-0000-000000000000")


def test_route_override_is_honoured(test_backend):
    result = test_backend.enqueue(add.using(queue_name="documents"), (1, 2), {})
    record = TaskRecord.objects.using("postgres").get(pk=result.id)
    assert record.queue_name == "documents"
    assert add.queue_name == "short"  # global default unchanged


def test_unknown_route_is_rejected(test_backend):
    with pytest.raises(InvalidTask):
        test_backend.enqueue(add.using(queue_name="unknown"), (1, 2), {})


def test_restricted_route_is_rejected_by_policy(test_backend):
    render_backend = backend_with_policy_override(
        {"tests.demo_tasks.render": {"allowed_routes": ["documents"]}}
    )
    with pytest.raises(ValueError):
        render_backend.enqueue(render.using(queue_name="short"), ("d1",), {})


def test_operation_key_survives_enqueue_and_replay(monkeypatch):
    from django.utils import timezone

    from django_tasks_aws.services import control, dispatch

    monkeypatch.setattr(
        dispatch, "publish_task", lambda *args, **kwargs: dispatch.DispatchResult.DISPATCHED
    )
    backend = backend_with_policy_override(
        {"tests.demo_tasks.add": {"operation_key_resolver": "tests.demo_tasks.add_operation_key"}}
    )
    accepted = backend.enqueue(add, (1, 2), {})
    original = TaskRecord.objects.using("postgres").get(pk=accepted.id)
    assert original.operation_key == "add:1+2"

    TaskRecord.objects.using("postgres").filter(pk=accepted.id).update(
        status=TaskStatus.FAILED, finished_at=timezone.now()
    )
    request = control.ControlRequest("replay-1", "investigated", "operator", effects_reviewed=True)
    result = control.operate(
        backend=backend, task_id=str(accepted.id), action="replay", request=request
    )
    replacement = TaskRecord.objects.using("postgres").get(pk=result.replacement_task_id)
    assert replacement.operation_key == "add:1+2"
    assert str(replacement.replay_of_id) == accepted.id


def backend_with_policy_override(overrides):
    from django_tasks_aws.backends import DatabaseSqsBackend
    from tests.conftest import make_manifest

    return DatabaseSqsBackend(
        "default",
        {
            "QUEUES": ["short", "documents"],
            "OPTIONS": {
                "database_alias": "postgres",
                "workers": make_manifest(),
                "allowed_task_modules": ["tests.demo_tasks"],
                "policies": overrides,
            },
        },
    )
