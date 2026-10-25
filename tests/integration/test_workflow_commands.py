"""Workflow inspection and recovery management commands."""

import json
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from django_tasks_aws import policies
from django_tasks_aws.models import TaskRecord, TaskStatus
from django_tasks_aws.services import claims, execution
from django_tasks_aws.workflows import Workflow, step
from django_tasks_aws.workflows.models import NodeStatus, RunStatus, WorkflowNode, WorkflowRun
from django_tasks_aws.workflows.services import advance_workflow
from tests.demo_tasks import add

DB = "postgres"

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def run_command(name, *args):
    out, err = StringIO(), StringIO()
    call_command(name, *args, "--json", stdout=out, stderr=err)
    return json.loads(out.getvalue()), err.getvalue()


def workflow_nodes(workflow_id):
    return list(WorkflowNode.objects.using(DB).filter(run_id=workflow_id).order_by("position"))


def find_workflow_run(workflow_id):
    return WorkflowRun.objects.using(DB).get(pk=workflow_id)


def execute(node):
    claimed = claims.claim_task(
        task_id=node.task_id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias=DB,
    )
    record = TaskRecord.objects.using(DB).get(pk=node.task_id)
    policy = policies.TaskPolicy(
        task_path="*", allowed_routes=("short", "documents"), payload_versions=(1,), max_attempts=5
    )
    return execution.execute_claimed_task(
        claimed=claimed,
        record=record,
        policy=policy,
        allowed_modules=("tests.demo_tasks",),
        database_alias=DB,
    )


def fail_node(node):
    TaskRecord.objects.using(DB).filter(pk=node.task_id).update(
        status=TaskStatus.FAILED, last_error={"error_type": "boom"}, finished_at=timezone.now()
    )


def test_workflows_list_and_show():
    run = Workflow(step(add, 1, 1).named("first") >> step(add, 2, 2), name="pipeline").enqueue()

    listed, _ = run_command("workflows_list")
    assert any(item["name"] == "pipeline" for item in listed["runs"])

    shown, _ = run_command("workflows_show", str(run.pk))
    assert shown["status"] == RunStatus.RUNNING
    assert shown["nodes"][0]["name"] == "first"
    assert shown["nodes"][1]["blocking_reason"] == "waiting for first"


def test_workflows_list_filters_by_name():
    Workflow(step(add, 1, 1), name="alpha").enqueue()
    Workflow(step(add, 2, 2), name="beta").enqueue()

    result, _ = run_command("workflows_list", "--name", "alpha")

    assert [item["name"] for item in result["runs"]] == ["alpha"]


def test_workflows_retry_dry_run_safe_and_idempotent():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)
    execute(nodes[0])
    nodes = workflow_nodes(run.pk)
    fail_node(nodes[1])
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.FAILED

    preview, _ = run_command(
        "workflows_retry",
        str(run.pk),
        nodes[1].key,
        "--reason",
        "fix",
        "--request-id",
        "cli-retry-1",
        "--dry-run",
    )
    assert preview["dry_run"] is True
    assert workflow_nodes(run.pk)[1].status == NodeStatus.FAILED

    applied, _ = run_command(
        "workflows_retry",
        str(run.pk),
        nodes[1].key,
        "--reason",
        "fix",
        "--request-id",
        "cli-retry-1",
    )
    assert applied["repeated"] is False
    assert workflow_nodes(run.pk)[1].status == NodeStatus.READY

    again, _ = run_command(
        "workflows_retry",
        str(run.pk),
        nodes[1].key,
        "--reason",
        "fix",
        "--request-id",
        "cli-retry-1",
    )
    assert again["repeated"] is True


def test_workflows_cancel_dry_run_and_apply():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()

    preview, _ = run_command(
        "workflows_cancel",
        str(run.pk),
        "--reason",
        "stop",
        "--request-id",
        "cli-cancel-1",
        "--dry-run",
    )
    assert preview["dry_run"] is True
    assert find_workflow_run(run.pk).status == RunStatus.RUNNING

    applied, _ = run_command(
        "workflows_cancel",
        str(run.pk),
        "--reason",
        "stop",
        "--request-id",
        "cli-cancel-1",
    )
    assert applied["settled"] is True
    assert find_workflow_run(run.pk).status == RunStatus.CANCELLED


def test_workflows_show_missing_run_is_rejected():
    with pytest.raises(CommandError):
        run_command("workflows_show", str(uuid4()))
