"""Every enqueue entry point applies the same acceptance rules before persistence."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.tasks.exceptions import InvalidTask

from django_tasks_aws import backends, policies
from django_tasks_aws.exceptions import TaskResolutionError
from django_tasks_aws.services import enqueue as acceptance
from django_tasks_aws.workflows import aws
from django_tasks_aws.workflows.exceptions import PreparationError
from tests.demo_tasks import add

ENTRY_POINTS = ("backend", "submit", "workflow", "retry", "service")


@pytest.fixture
def accepted_records(test_backend, monkeypatch):
    records = []

    def accept(backend, record):
        records.append(record)
        return acceptance.EnqueueOutcome(record, True)

    monkeypatch.setattr(acceptance, "accept_record", accept)
    monkeypatch.setattr(acceptance, "report_acceptance", Mock())
    monkeypatch.setattr(acceptance, "task_backends", {"default": test_backend})
    monkeypatch.setattr(aws, "task_backends", {"default": test_backend})
    monkeypatch.setattr(backends, "get_task_result", lambda task, record: record)
    monkeypatch.setattr("django_tasks_aws.results.get_task_result", lambda task, record: record)
    return records


def invoke(entry, backend, monkeypatch):
    match entry:
        case "backend":
            return backend.enqueue(add, (1, 2), {})
        case "submit":
            return acceptance.submit(add, args=(1, 2), key="request", scope="tenant")
        case "service":
            return acceptance.enqueue(backend, task=add, args=(1, 2), kwargs={})
        case "workflow" | "retry":
            adapter = aws.AwsTaskAdapter("default")
            monkeypatch.setattr(adapter, "get_original_operation_key", lambda node: "effect")
            return invoke_workflow(entry, adapter)
        case _:
            raise AssertionError(entry)


def invoke_workflow(entry, adapter):
    node = SimpleNamespace(
        key="0",
        run_id=uuid4(),
        task_id=uuid4(),
        task_config={
            "path": add.module_path,
            "queue_name": add.queue_name,
            "priority": add.priority,
            "run_after": None,
            "takes_context": add.takes_context,
        },
    )
    if entry == "workflow":
        return adapter.enqueue(node, [1, 2], {})
    return adapter.retry(node, [1, 2], {}, request_id="retry-request")


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_entry_points_accept_the_same_task_contract(
    entry, test_backend, monkeypatch, accepted_records
):
    invoke(entry, test_backend, monkeypatch)
    assert len(accepted_records) == 1
    record = accepted_records[0]
    assert record.task_name == add.module_path
    assert record.task_options == {"takes_context": add.takes_context}
    assert record.payload == {"args": [1, 2], "kwargs": {}}
    assert record.queue_name == add.queue_name
    assert record.backend_alias == test_backend.alias
    assert record.submission_hash
    if entry == "submit":
        assert (record.dedupe_key, record.dedupe_scope) == ("request", "tenant")
    if entry == "retry":
        assert record.operation_key == "effect"
        assert record.replay_of_id is not None
        assert record.dedupe_key == "retry-request"


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize("restriction", ("policy", "allowlist", "queue"))
def test_entry_points_reject_incompatible_work_before_storage(
    entry, restriction, test_backend, monkeypatch, accepted_records
):
    match restriction:
        case "policy":
            test_backend.policies = policies.build_policy_catalog(
                max_attempts=5,
                allowed_routes=("documents",),
                overrides=None,
            )
        case "allowlist":
            test_backend.allowed_task_modules = ("other_application.tasks",)
        case "queue":
            test_backend.queues = {"documents"}
    expected = {
        "policy": ValueError,
        "allowlist": TaskResolutionError,
        "queue": InvalidTask,
    }[restriction]
    if entry in {"workflow", "retry"}:
        expected = PreparationError
    with pytest.raises(expected):
        invoke(entry, test_backend, monkeypatch)
    assert accepted_records == []
    acceptance.report_acceptance.assert_not_called()
