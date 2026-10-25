"""Durable workflow acceptance, advancement, recovery, and retention.

Real-PostgreSQL evidence: workflow submission is transactional and idempotent,
advancement is serialized against competing callers, and sweeper recovery
reconciles runs whose completion callbacks were lost. Dispatch is stubbed so the
suite never reaches SQS.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connections, transaction
from django.utils import timezone

from django_tasks_aws import policies
from django_tasks_aws.models import HoldReason, TaskRecord, TaskStatus
from django_tasks_aws.services import claims, dispatch, execution
from django_tasks_aws.services.control import ControlRequest, operate
from django_tasks_aws.services.dispatch import DispatchResult
from django_tasks_aws.workflows import Workflow, get_node_output, get_run_status, parallel, step
from django_tasks_aws.workflows.exceptions import (
    WorkflowConflict,
    WorkflowError,
    WorkflowNotFound,
    WorkflowRecoveryError,
)
from django_tasks_aws.workflows.models import (
    NodeStatus,
    RunStatus,
    WorkflowAction,
    WorkflowNode,
    WorkflowRun,
)
from django_tasks_aws.workflows.services import (
    advance_workflow,
    cancel_workflow,
    expire_workflows,
    reconcile_workflows,
    retry_workflow_step,
)
from tests.demo_tasks import add, record_result, render

DB = "postgres"

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


@pytest.fixture(autouse=True)
def stub_dispatch(monkeypatch):
    monkeypatch.setattr(dispatch, "publish_task", lambda *a, **k: DispatchResult.DISPATCHED)


def workflow_nodes(workflow_id):
    return list(WorkflowNode.objects.using(DB).filter(run_id=workflow_id).order_by("position"))


def find_workflow_run(workflow_id):
    return WorkflowRun.objects.using(DB).get(pk=workflow_id)


def complete(node, *, result=None):
    TaskRecord.objects.using(DB).filter(pk=node.task_id).update(
        status=TaskStatus.SUCCESSFUL, result=result, finished_at=timezone.now()
    )


def fail(node):
    TaskRecord.objects.using(DB).filter(pk=node.task_id).update(
        status=TaskStatus.FAILED, last_error={"error_type": "boom"}, finished_at=timezone.now()
    )


def hold(node):
    TaskRecord.objects.using(DB).filter(pk=node.task_id).update(
        held_at=timezone.now(), hold_reason=HoldReason.DELIVERY_EXHAUSTED
    )


def default_policy(**overrides) -> policies.TaskPolicy:
    kwargs = {
        "task_path": "*",
        "allowed_routes": ("short", "documents"),
        "payload_versions": (1,),
        "max_attempts": 5,
    }
    kwargs.update(overrides)
    return policies.TaskPolicy(**kwargs)


def execute(node, *, policy=None) -> str:
    """Claim and execute a node's task through the real lifecycle services."""
    queue = node.task_config["queue_name"]
    claimed = claims.claim_task(
        task_id=node.task_id,
        queue_name=queue,
        worker_name=queue,
        lease_seconds=90,
        database_alias=DB,
    )
    assert claimed is not None
    record = TaskRecord.objects.using(DB).get(pk=node.task_id)
    return execution.execute_claimed_task(
        claimed=claimed,
        record=record,
        policy=policy or default_policy(),
        allowed_modules=("tests.demo_tasks",),
        database_alias=DB,
    )


def test_rollback_creates_no_workflow():
    with transaction.atomic(using=DB):
        Workflow(step(add, 1, 2)).enqueue()
        transaction.set_rollback(True, using=DB)

    assert WorkflowRun.objects.using(DB).count() == 0
    assert WorkflowNode.objects.using(DB).count() == 0
    assert TaskRecord.objects.using(DB).count() == 0


def test_duplicate_submission_returns_same_run():
    first = Workflow(step(add, 1, 2)).enqueue(submission_key="wf-1")
    second = Workflow(step(add, 1, 2)).enqueue(submission_key="wf-1")

    assert first.pk == second.pk
    assert WorkflowRun.objects.using(DB).count() == 1


def test_changed_definition_conflicts():
    Workflow(step(add, 1, 2)).enqueue(submission_key="wf-2")

    with pytest.raises(WorkflowConflict):
        Workflow(step(add, 3, 4)).enqueue(submission_key="wf-2")


def test_multiple_unkeyed_workflows_are_distinct():
    first = Workflow(step(add, 1, 2)).enqueue()
    second = Workflow(step(add, 3, 4)).enqueue()

    assert first.pk != second.pk
    assert WorkflowRun.objects.using(DB).count() == 2


def test_cycle_is_rejected():
    a = step(add, 1, 1)
    b = step(add, 2, 2)

    with pytest.raises(WorkflowError):
        Workflow((a >> b) >> a).enqueue()


def test_inferred_dependencies_include_ancestors():
    first = step(add, 2, 3)
    child = step(add, first.output, 1)
    run = Workflow(child).enqueue()

    nodes = workflow_nodes(run.pk)
    assert len(nodes) == 2
    dependent = [node for node in nodes if node.incoming.using(DB).exists()]
    assert len(dependent) == 1
    assert dependent[0].incoming.using(DB).values_list("parent__key", flat=True).first() is not None


def test_node_limit_is_rejected(monkeypatch):
    from django_tasks_aws.workflows import definition as definition_module
    from django_tasks_aws.workflows.configuration import WorkflowLimits

    monkeypatch.setattr(
        definition_module, "get_workflow_limits", lambda: WorkflowLimits(max_nodes=1)
    )

    with pytest.raises(WorkflowError):
        Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()


def test_advancement_schedules_across_batches(monkeypatch):
    from django_tasks_aws.workflows import services as services_module
    from django_tasks_aws.workflows.configuration import WorkflowLimits

    graph = step(add, 1, 1) >> parallel(step(add, 2, 2), step(add, 3, 3), step(add, 4, 4))
    run = Workflow(graph).enqueue()
    complete(workflow_nodes(run.pk)[0], result=2)

    monkeypatch.setattr(
        services_module, "get_workflow_limits", lambda: WorkflowLimits(advancement_batch=1)
    )

    advance_workflow(run.pk, database_alias=DB)
    assert [n.status for n in workflow_nodes(run.pk)[1:]].count(NodeStatus.READY) == 1
    advance_workflow(run.pk, database_alias=DB)
    assert [n.status for n in workflow_nodes(run.pk)[1:]].count(NodeStatus.READY) == 2
    advance_workflow(run.pk, database_alias=DB)
    assert [n.status for n in workflow_nodes(run.pk)[1:]].count(NodeStatus.READY) == 3


def test_single_node_reaches_successful():
    run = Workflow(step(add, 1, 2)).enqueue()
    (node,) = workflow_nodes(run.pk)

    assert node.status == NodeStatus.READY
    assert node.task_id is not None

    complete(node, result=3)
    advance_workflow(run.pk, database_alias=DB)

    assert workflow_nodes(run.pk)[0].status == NodeStatus.SUCCESSFUL
    assert find_workflow_run(run.pk).status == RunStatus.SUCCESSFUL


def test_fan_out_fan_in():
    graph = step(add, 1, 1) >> parallel(step(add, 2, 2), step(add, 3, 3)) >> step(add, 4, 4)
    run = Workflow(graph).enqueue()
    nodes = workflow_nodes(run.pk)

    assert [node.status for node in nodes] == [
        NodeStatus.READY,
        NodeStatus.WAITING,
        NodeStatus.WAITING,
        NodeStatus.WAITING,
    ]

    complete(nodes[0])
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)
    assert [node.status for node in nodes] == [
        NodeStatus.SUCCESSFUL,
        NodeStatus.READY,
        NodeStatus.READY,
        NodeStatus.WAITING,
    ]

    complete(nodes[1])
    complete(nodes[2])
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)
    assert [node.status for node in nodes] == [
        NodeStatus.SUCCESSFUL,
        NodeStatus.SUCCESSFUL,
        NodeStatus.SUCCESSFUL,
        NodeStatus.READY,
    ]

    complete(nodes[3])
    advance_workflow(run.pk, database_alias=DB)
    assert all(node.status == NodeStatus.SUCCESSFUL for node in workflow_nodes(run.pk))
    assert find_workflow_run(run.pk).status == RunStatus.SUCCESSFUL


def test_output_passed_from_parent_to_child():
    first = step(add, 2, 3)
    child = step(add, first.output, 1)
    run = Workflow(first >> child).enqueue()
    nodes = workflow_nodes(run.pk)

    complete(nodes[0], result=5)
    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.READY
    payload = TaskRecord.objects.using(DB).get(pk=nodes[1].task_id).payload
    assert payload["args"] == [5, 1]


def test_output_dict_path_selection():
    first = step(record_result, 7)
    child = step(add, first.output["value"], 1)
    run = Workflow(first >> child).enqueue()
    nodes = workflow_nodes(run.pk)

    complete(nodes[0], result={"value": 7})
    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    payload = TaskRecord.objects.using(DB).get(pk=nodes[1].task_id).payload
    assert payload["args"] == [7, 1]


def test_preparation_failure_marks_node_failed():
    first = step(record_result, 7)
    child = step(add, first.output["missing"], 1)
    run = Workflow(first >> child).enqueue()
    nodes = workflow_nodes(run.pk)

    complete(nodes[0], result={"value": 7})
    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.FAILED
    assert nodes[1].error["error_type"] == "PreparationError"
    assert find_workflow_run(run.pk).status == RunStatus.FAILED


def test_held_parent_blocks_descendant():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)

    hold(nodes[0])
    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.BLOCKED
    assert nodes[1].status == NodeStatus.WAITING
    assert find_workflow_run(run.pk).status == RunStatus.BLOCKED


def test_failed_descendant_is_skipped():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)

    fail(nodes[0])
    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.FAILED
    assert nodes[1].status == NodeStatus.SKIPPED
    assert find_workflow_run(run.pk).status == RunStatus.FAILED


def test_missing_task_marks_node_failed():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)

    TaskRecord.objects.using(DB).filter(pk=nodes[0].task_id).delete()
    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.FAILED
    assert nodes[0].error["error_type"] == "MissingTask"
    assert nodes[1].status == NodeStatus.SKIPPED


def test_retired_queue_records_preparation_failure(monkeypatch):
    from django.tasks import task_backends

    run = Workflow(step(add, 1, 1) >> step(render, "doc-1")).enqueue()
    nodes = workflow_nodes(run.pk)
    complete(nodes[0], result=2)

    backend = task_backends["default"]
    monkeypatch.setattr(backend, "queues", {"short"})

    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.FAILED
    assert nodes[1].error["error_type"] == "PreparationError"
    assert nodes[1].task_id is None
    assert nodes[1].task_config["queue_name"] == "documents"
    assert nodes[1].arguments == [
        "dict",
        [["args", ["list", [["literal", "doc-1"]]]], ["kwargs", ["dict", []]]],
    ]
    assert find_workflow_run(run.pk).status == RunStatus.FAILED


def test_retired_queue_failure_does_not_block_other_runs(monkeypatch):
    from django.tasks import task_backends

    broken = Workflow(step(add, 1, 1) >> step(render, "doc-1")).enqueue()
    healthy = Workflow(step(add, 2, 2) >> step(add, 3, 3)).enqueue()
    complete(workflow_nodes(broken.pk)[0], result=2)
    complete(workflow_nodes(healthy.pk)[0], result=4)

    backend = task_backends["default"]
    monkeypatch.setattr(backend, "queues", {"short"})

    processed = reconcile_workflows(database_alias=DB, backend_alias="default")

    assert processed == 2
    assert workflow_nodes(healthy.pk)[1].status == NodeStatus.READY
    assert workflow_nodes(healthy.pk)[1].task_id is not None
    assert workflow_nodes(broken.pk)[1].status == NodeStatus.FAILED
    assert workflow_nodes(broken.pk)[1].error["error_type"] == "PreparationError"
    assert find_workflow_run(broken.pk).status == RunStatus.FAILED
    assert find_workflow_run(healthy.pk).status == RunStatus.RUNNING


def test_changed_takes_context_records_preparation_failure(monkeypatch):
    from dataclasses import replace

    from django_tasks_aws.workflows import aws as aws_module

    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)
    complete(nodes[0], result=2)

    original = aws_module.resolve_task

    def changed_contract(path, allowed_modules):
        task = original(path, allowed_modules=allowed_modules)
        return replace(task, takes_context=True)

    monkeypatch.setattr(aws_module, "resolve_task", changed_contract)

    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.FAILED
    assert nodes[1].error["error_type"] == "PreparationError"


def test_sweeper_recovers_lost_completion_callback():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)

    complete(nodes[0], result=2)
    processed = reconcile_workflows(database_alias=DB, backend_alias="default")

    assert processed == 1
    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.SUCCESSFUL
    assert nodes[1].status == NodeStatus.READY


def test_concurrent_advancement_schedules_once():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)
    complete(nodes[0], result=2)

    def advance(_):
        try:
            return advance_workflow(run.pk, database_alias=DB)
        finally:
            connections[DB].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(advance, range(2)))

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.READY
    assert nodes[1].task_id is not None
    assert TaskRecord.objects.using(DB).filter(dedupe_scope=f"workflow:{run.pk}").count() == 2


def test_active_workflow_tasks_are_protected_from_retention():
    from django_tasks_aws.workflows.aws import protect_workflow_results

    Workflow(step(add, 1, 1)).enqueue()
    records = TaskRecord.objects.using(DB).all()

    filtered = protect_workflow_results(sender=None, records=records)
    assert list(filtered.values_list("pk", flat=True)) == []


def test_terminal_workflow_tasks_are_protected_until_workflow_redaction():
    from django_tasks_aws.workflows.aws import protect_workflow_results

    run = Workflow(step(add, 1, 1)).enqueue()
    (node,) = workflow_nodes(run.pk)
    complete(node, result=2)
    advance_workflow(run.pk, database_alias=DB)

    records = TaskRecord.objects.using(DB).all()
    filtered = protect_workflow_results(sender=None, records=records)
    assert list(filtered.values_list("pk", flat=True)) == []
    WorkflowRun.objects.using(DB).filter(pk=run.pk).update(details_expired_at=timezone.now())
    assert list(
        protect_workflow_results(sender=None, records=records).values_list("pk", flat=True)
    ) == [node.task_id]


def test_real_execution_advances_chain():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)

    outcome = execute(nodes[0])
    assert outcome == "success"

    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.SUCCESSFUL
    assert nodes[1].status == NodeStatus.READY


def test_real_execution_passes_resolved_output():
    first = step(add, 2, 3)
    run = Workflow(first >> step(add, first.output, 1)).enqueue()
    nodes = workflow_nodes(run.pk)

    execute(nodes[0])

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.READY
    payload = TaskRecord.objects.using(DB).get(pk=nodes[1].task_id).payload
    assert payload["args"] == [5, 1]


def test_real_execution_terminal_failure_skips_descendant():
    from tests.demo_tasks import fail_permanently

    run = Workflow(step(fail_permanently, "x") >> step(add, 1, 1)).enqueue()
    nodes = workflow_nodes(run.pk)

    outcome = execute(nodes[0])
    assert outcome == "failure"

    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.FAILED
    assert nodes[1].status == NodeStatus.SKIPPED
    assert find_workflow_run(run.pk).status == RunStatus.FAILED


def test_real_execution_ambiguous_outcome_blocks_run():
    from tests.demo_tasks import ambiguous

    run = Workflow(step(ambiguous, "x") >> step(add, 1, 1)).enqueue()
    nodes = workflow_nodes(run.pk)

    outcome = execute(nodes[0])
    assert outcome == "hold"

    nodes = workflow_nodes(run.pk)
    assert nodes[0].status == NodeStatus.BLOCKED
    assert nodes[1].status == NodeStatus.WAITING
    assert find_workflow_run(run.pk).status == RunStatus.BLOCKED


def test_real_execution_retry_keeps_node_ready():
    from tests.demo_tasks import fail_transiently

    run = Workflow(step(fail_transiently, "x")).enqueue()
    (node,) = workflow_nodes(run.pk)

    retryable = default_policy(retryable_exception_paths=("builtins.RuntimeError",))
    outcome = execute(node, policy=retryable)
    assert outcome == "retry"

    node = workflow_nodes(run.pk)[0]
    assert node.status == NodeStatus.READY
    record = TaskRecord.objects.using(DB).get(pk=node.task_id)
    assert record.status == TaskStatus.PENDING
    assert record.attempts_started == 1

    record.available_at = timezone.now()
    record.next_dispatch_at = timezone.now()
    record.save(using=DB, update_fields=["available_at", "next_dispatch_at"])
    outcome = execute(workflow_nodes(run.pk)[0], policy=retryable)
    assert outcome == "retry"
    assert TaskRecord.objects.using(DB).get(pk=node.task_id).attempts_started == 2


def test_audited_release_unblocks_workflow():
    from django.tasks import task_backends

    from django_tasks_aws.services import outcomes

    run = Workflow(step(add, 1, 1)).enqueue()
    (node,) = workflow_nodes(run.pk)
    outcomes.hold_pending(
        task_id=str(node.task_id),
        hold_reason=HoldReason.DELIVERY_EXHAUSTED,
        database_alias=DB,
    )
    advance_workflow(run.pk, database_alias=DB)
    assert workflow_nodes(run.pk)[0].status == NodeStatus.BLOCKED

    result = operate(
        backend=task_backends["default"],
        task_id=str(node.task_id),
        action="release",
        request=ControlRequest(
            request_id="wf-release-1",
            reason="release delivery hold",
            actor_reference="tester",
            reset_delivery=True,
        ),
    )
    assert not result.repeated
    advance_workflow(run.pk, database_alias=DB)

    node = workflow_nodes(run.pk)[0]
    assert node.status == NodeStatus.READY
    execute(node)
    assert find_workflow_run(run.pk).status == RunStatus.SUCCESSFUL


def test_child_scheduling_failure_rolls_back_submission(monkeypatch):
    from django_tasks_aws.workflows import aws as aws_module

    calls = {"count": 0}
    original = aws_module.AwsTaskAdapter.enqueue

    def flaky(self, node, args, kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("scheduling failure")
        return original(self, node, args, kwargs)

    monkeypatch.setattr(aws_module.AwsTaskAdapter, "enqueue", flaky)

    with pytest.raises(RuntimeError):
        Workflow(parallel(step(add, 1, 1), step(add, 2, 2))).enqueue()

    assert WorkflowRun.objects.using(DB).count() == 0
    assert WorkflowNode.objects.using(DB).count() == 0
    assert TaskRecord.objects.using(DB).count() == 0


def test_workflow_cleanup_redacts_terminal_details():
    run = Workflow(step(add, 1, 1)).enqueue()
    (node,) = workflow_nodes(run.pk)
    complete(node, result=2)
    advance_workflow(run.pk, database_alias=DB)
    WorkflowRun.objects.using(DB).filter(pk=run.pk).update(
        finished_at=timezone.now() - timedelta(days=31)
    )

    redacted = expire_workflows(database_alias=DB, backend_alias="default")

    assert redacted == 1
    node = workflow_nodes(run.pk)[0]
    assert node.arguments == []
    assert node.error == {}
    assert find_workflow_run(run.pk).details_expired_at is not None


def test_deadline_defers_child_scheduling():
    import time

    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    complete(workflow_nodes(run.pk)[0], result=2)

    advance_workflow(run.pk, database_alias=DB, deadline=time.monotonic())

    nodes = workflow_nodes(run.pk)
    assert TaskRecord.objects.using(DB).get(pk=nodes[0].task_id).status == TaskStatus.SUCCESSFUL
    assert nodes[1].status == NodeStatus.WAITING
    assert nodes[1].task_id is None

    advance_workflow(run.pk, database_alias=DB)

    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.READY
    assert TaskRecord.objects.using(DB).filter(dedupe_scope=f"workflow:{run.pk}").count() == 2


def test_reconcile_with_expired_deadline_skips_all():
    import time

    Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()

    processed = reconcile_workflows(
        database_alias=DB, backend_alias="default", deadline=time.monotonic()
    )

    assert processed == 0


def test_completion_callback_threads_deadline(monkeypatch):
    from django_tasks_aws.workflows import services as services_module
    from django_tasks_aws.workflows.aws import advance_after_task

    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    (node,) = workflow_nodes(run.pk)[:1]

    deadlines = []

    def spy(workflow_id, *, database_alias, deadline=None):
        deadlines.append(deadline)

    monkeypatch.setattr(services_module, "advance_workflow", spy)

    advance_after_task(sender=None, task_id=node.task_id, database_alias=DB, deadline=42.0)

    assert deadlines == [42.0]


def test_audited_retry_reopens_step_and_descendants():
    a = step(add, 2, 3)
    b = step(add, a.output, 1)
    run = Workflow(a >> b >> step(add, 4, 4)).enqueue()
    nodes = workflow_nodes(run.pk)

    execute(nodes[0])
    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.READY
    a_task_id = nodes[0].task_id
    original_b_task_id = nodes[1].task_id

    fail(nodes[1])
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.FAILED
    assert nodes[2].status == NodeStatus.SKIPPED
    assert find_workflow_run(run.pk).status == RunStatus.FAILED

    outcome = retry_workflow_step(
        run.pk,
        nodes[1].key,
        request_id="retry-1",
        reason="fix b",
        actor_reference="tester",
        database_alias=DB,
    )

    assert not outcome.repeated
    nodes = workflow_nodes(run.pk)
    assert nodes[1].status == NodeStatus.READY
    assert nodes[1].task_id not in (None, original_b_task_id)
    assert nodes[2].status == NodeStatus.WAITING
    assert find_workflow_run(run.pk).status == RunStatus.RUNNING
    assert nodes[0].task_id == a_task_id
    assert TaskRecord.objects.using(DB).get(pk=original_b_task_id).status == TaskStatus.FAILED
    assert TaskRecord.objects.using(DB).get(pk=nodes[1].task_id).replay_of_id == original_b_task_id

    execute(nodes[1])
    assert workflow_nodes(run.pk)[2].status == NodeStatus.READY


def test_retry_is_idempotent():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)
    execute(nodes[0])
    nodes = workflow_nodes(run.pk)
    fail(nodes[1])
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)

    first = retry_workflow_step(
        run.pk,
        nodes[1].key,
        request_id="retry-2",
        reason="x",
        actor_reference="t",
        database_alias=DB,
    )
    second = retry_workflow_step(
        run.pk,
        nodes[1].key,
        request_id="retry-2",
        reason="x",
        actor_reference="t",
        database_alias=DB,
    )

    assert second.repeated
    assert second.replacement_task_id == first.replacement_task_id
    assert WorkflowAction.objects.using(DB).filter(run=run).count() == 1


def test_retry_uncertain_failure_requires_review():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    nodes = workflow_nodes(run.pk)
    execute(nodes[0])
    nodes = workflow_nodes(run.pk)
    TaskRecord.objects.using(DB).filter(pk=nodes[1].task_id).update(
        status=TaskStatus.FAILED,
        last_error={"error_type": "boom", "uncertain": True},
        finished_at=timezone.now(),
    )
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)

    with pytest.raises(WorkflowRecoveryError):
        retry_workflow_step(
            run.pk,
            nodes[1].key,
            request_id="r3",
            reason="x",
            actor_reference="t",
            database_alias=DB,
        )

    outcome = retry_workflow_step(
        run.pk,
        nodes[1].key,
        request_id="r3",
        reason="x",
        actor_reference="t",
        effects_reviewed=True,
        database_alias=DB,
    )
    assert not outcome.repeated


def test_retry_rejects_expired_parent_results():
    a = step(add, 2, 3)
    b = step(add, a.output, 1)
    run = Workflow(a >> b).enqueue()
    nodes = workflow_nodes(run.pk)
    execute(nodes[0])
    nodes = workflow_nodes(run.pk)
    fail(nodes[1])
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)
    TaskRecord.objects.using(DB).filter(pk=nodes[0].task_id).update(
        details_expired_at=timezone.now()
    )

    with pytest.raises(WorkflowRecoveryError):
        retry_workflow_step(
            run.pk,
            nodes[1].key,
            request_id="r4",
            reason="x",
            actor_reference="t",
            database_alias=DB,
        )


def test_display_names_are_presentation_not_identity():
    run = Workflow(
        step(add, 1, 1).named("increment"),
        name="pipeline",
        business_reference="ref-1",
    ).enqueue(submission_key="wf-named")

    assert find_workflow_run(run.pk).name == "pipeline"
    assert find_workflow_run(run.pk).business_reference == "ref-1"
    assert any(node.name == "increment" for node in workflow_nodes(run.pk))

    again = Workflow(step(add, 1, 1).named("renamed")).enqueue(
        submission_key="wf-named", name="renamed-pipeline"
    )
    assert again.pk == run.pk


def test_invalid_display_name_is_rejected():
    with pytest.raises(WorkflowError):
        Workflow(step(add, 1, 1)).enqueue(name="   ")


def test_run_status_reports_progress_and_blocking():
    run = Workflow(step(add, 1, 1).named("first") >> step(add, 2, 2).named("second")).enqueue()

    snapshot = get_run_status(run.pk, database_alias=DB)

    assert snapshot.status == RunStatus.RUNNING
    assert len(snapshot.nodes) == 2
    assert snapshot.nodes[0].name == "first"
    assert snapshot.nodes[0].status == NodeStatus.READY
    assert snapshot.nodes[1].name == "second"
    assert snapshot.nodes[1].status == NodeStatus.WAITING
    assert snapshot.nodes[1].blocking_reason == "waiting for first"
    assert snapshot.nodes[1].parents == ("0",)


def test_run_status_omits_arguments_and_results():
    run = Workflow(step(add, 1, 1)).enqueue()

    snapshot = get_run_status(run.pk, database_alias=DB)

    node = snapshot.nodes[0]
    assert not hasattr(node, "arguments")
    assert not hasattr(node, "result")


def test_node_output_retrieves_successful_result():
    run = Workflow(step(add, 2, 3).named("sum")).enqueue()
    (node,) = workflow_nodes(run.pk)
    complete(node, result=5)
    advance_workflow(run.pk, database_alias=DB)

    assert get_node_output(run.pk, node.key, database_alias=DB) == 5


def test_node_output_rejects_unsuccessful_and_missing():
    from uuid import uuid4

    run = Workflow(step(add, 2, 3).named("sum")).enqueue()
    (node,) = workflow_nodes(run.pk)

    with pytest.raises(WorkflowError):
        get_node_output(run.pk, node.key, database_alias=DB)
    with pytest.raises(WorkflowError):
        get_node_output(run.pk, "missing", database_alias=DB)
    with pytest.raises(WorkflowNotFound):
        get_run_status(uuid4(), database_alias=DB)


def test_cancel_workflow_marks_unrun_nodes_and_holds_pending():
    run = Workflow(step(add, 1, 1).named("a") >> step(add, 2, 2).named("b")).enqueue()
    nodes = workflow_nodes(run.pk)

    outcome = cancel_workflow(
        run.pk, request_id="cancel-1", reason="stop", actor_reference="tester", database_alias=DB
    )

    assert not outcome.repeated
    assert outcome.settled
    assert find_workflow_run(run.pk).status == RunStatus.CANCELLED
    nodes = workflow_nodes(run.pk)
    assert all(node.status == NodeStatus.SKIPPED for node in nodes)
    assert all(node.error["error_type"] == "Cancelled" for node in nodes)
    record = TaskRecord.objects.using(DB).get(pk=nodes[0].task_id)
    assert record.held_at is not None
    assert record.hold_reason == HoldReason.CANCELLED


def test_cancel_preserves_running_lease_and_settles_later():
    from django_tasks_aws.services import claims as claims_service

    run = Workflow(step(add, 1, 1).named("a") >> step(add, 2, 2).named("b")).enqueue()
    nodes = workflow_nodes(run.pk)
    execute(nodes[0])
    nodes = workflow_nodes(run.pk)
    claimed = claims_service.claim_task(
        task_id=nodes[1].task_id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias=DB,
    )
    assert claimed is not None

    outcome = cancel_workflow(
        run.pk, request_id="cancel-2", reason="stop", actor_reference="tester", database_alias=DB
    )

    assert not outcome.settled
    assert find_workflow_run(run.pk).status == RunStatus.RUNNING
    assert workflow_nodes(run.pk)[1].status == NodeStatus.RUNNING

    record = TaskRecord.objects.using(DB).get(pk=nodes[1].task_id)
    record.status = TaskStatus.SUCCESSFUL
    record.finished_at = timezone.now()
    record.lease_token = None
    record.lease_expires_at = None
    record.result = 4
    record.save(using=DB)
    advance_workflow(run.pk, database_alias=DB)

    assert find_workflow_run(run.pk).status == RunStatus.CANCELLED


def test_cancel_is_idempotent():
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()

    first = cancel_workflow(
        run.pk, request_id="cancel-3", reason="stop", actor_reference="tester", database_alias=DB
    )
    second = cancel_workflow(
        run.pk, request_id="cancel-3", reason="stop", actor_reference="tester", database_alias=DB
    )

    assert second.repeated
    assert second.settled == first.settled


def test_cancel_terminal_run_is_rejected():
    run = Workflow(step(add, 1, 1)).enqueue()
    (node,) = workflow_nodes(run.pk)
    complete(node, result=2)
    advance_workflow(run.pk, database_alias=DB)
    assert find_workflow_run(run.pk).status == RunStatus.SUCCESSFUL

    with pytest.raises(WorkflowRecoveryError):
        cancel_workflow(
            run.pk,
            request_id="cancel-4",
            reason="stop",
            actor_reference="tester",
            database_alias=DB,
        )


@pytest.mark.parametrize(
    "changed",
    ["node", "reason", "actor_reference", "effects_reviewed", "origin", "actor_kind", "action"],
)
def test_recovery_request_conflicts_on_changed_intent(changed):
    run = Workflow(step(add, 1, 1) & step(add, 2, 2)).enqueue()
    for node in workflow_nodes(run.pk):
        fail(node)
    advance_workflow(run.pk, database_alias=DB)
    nodes = workflow_nodes(run.pk)
    kwargs = dict(
        request_id="intent", reason="reviewed", actor_reference="tester", database_alias=DB
    )
    retry_workflow_step(run.pk, nodes[0].key, **kwargs)
    if changed == "action":

        def operation():
            return cancel_workflow(run.pk, **kwargs)
    elif changed == "node":

        def operation():
            return retry_workflow_step(run.pk, nodes[1].key, **kwargs)
    else:
        kwargs[changed] = True if changed == "effects_reviewed" else "changed"

        def operation():
            return retry_workflow_step(run.pk, nodes[0].key, **kwargs)

    with pytest.raises(WorkflowConflict):
        operation()
    assert WorkflowAction.objects.using(DB).filter(run=run).count() == 1


@pytest.mark.parametrize("operation", [cancel_workflow, retry_workflow_step])
def test_recovery_refuses_wrong_backend_and_missing_run(operation):
    from uuid import uuid4

    run = Workflow(step(add, 1, 1)).enqueue()
    args = (workflow_nodes(run.pk)[0].key,) if operation is retry_workflow_step else ()
    kwargs = dict(request_id="wrong", reason="review", actor_reference="tester", database_alias=DB)
    with pytest.raises(WorkflowNotFound):
        operation(run.pk, *args, backend_alias="other", **kwargs)
    with pytest.raises(WorkflowNotFound):
        operation(uuid4(), *args, **kwargs)
    assert not WorkflowAction.objects.using(DB).exists()


def test_parallel_cancellation_settles_after_real_worker_success():
    run = Workflow(step(add, 1, 1) & step(add, 2, 2)).enqueue()
    first, second = workflow_nodes(run.pk)
    claimed = claims.claim_task(
        task_id=first.task_id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias=DB,
    )
    assert claimed is not None
    record = TaskRecord.objects.using(DB).get(pk=first.task_id)
    outcome = cancel_workflow(
        run.pk, request_id="parallel", reason="stop", actor_reference="tester", database_alias=DB
    )
    assert not outcome.settled
    execution.execute_claimed_task(
        claimed=claimed,
        record=record,
        policy=default_policy(),
        allowed_modules=("tests.demo_tasks",),
        database_alias=DB,
    )
    assert find_workflow_run(run.pk).status == RunStatus.CANCELLED
    assert workflow_nodes(run.pk)[1].status == NodeStatus.SKIPPED
    advance_workflow(run.pk, database_alias=DB)
    # Even a core operator release must not revive work owned by a cancelled graph.
    from django.tasks import task_backends

    operate(
        backend=task_backends["default"],
        task_id=str(second.task_id),
        action="release",
        request=ControlRequest(
            request_id="release-cancelled",
            reason="reviewed",
            actor_reference="tester",
            effects_reviewed=True,
        ),
    )
    assert (
        claims.claim_task(
            task_id=second.task_id,
            queue_name="short",
            worker_name="short",
            lease_seconds=90,
            database_alias=DB,
        )
        is None
    )


def test_cancelled_failed_branch_cannot_be_retried():
    run = Workflow(step(add, 1, 1) & step(add, 2, 2)).enqueue()
    first, _ = workflow_nodes(run.pk)
    fail(first)
    advance_workflow(run.pk, database_alias=DB)
    cancel_workflow(
        run.pk, request_id="cancel", reason="stop", actor_reference="tester", database_alias=DB
    )
    with pytest.raises(WorkflowRecoveryError, match="Cancelled"):
        retry_workflow_step(
            run.pk,
            first.key,
            request_id="revive",
            reason="retry",
            actor_reference="tester",
            database_alias=DB,
        )


def test_retention_rechecks_reopened_run_under_lock():
    from django_tasks_aws.workflows.services import expire_workflow

    run = Workflow(step(add, 1, 1)).enqueue()
    (node,) = workflow_nodes(run.pk)
    fail(node)
    advance_workflow(run.pk, database_alias=DB)
    cutoff = timezone.now() - timedelta(days=30)
    WorkflowRun.objects.using(DB).filter(pk=run.pk).update(finished_at=cutoff - timedelta(days=1))
    retry_workflow_step(
        run.pk,
        node.key,
        request_id="reopen",
        reason="retry",
        actor_reference="tester",
        database_alias=DB,
    )
    assert not expire_workflow(run.pk, DB, cutoff)
    assert workflow_nodes(run.pk)[0].arguments
    assert find_workflow_run(run.pk).details_expired_at is None


def test_named_output_and_database_selection():
    run = Workflow(step(add, 1, 1).named("sum")).enqueue()
    execute(workflow_nodes(run.pk)[0])
    assert get_node_output(run.pk, "sum", database_alias=DB) == 2
    with pytest.raises(WorkflowError, match="database"):
        get_node_output(run.pk, "sum", database_alias="default")


def test_duplicate_output_names_require_explicit_key():
    run = Workflow(step(add, 1, 1).named("sum") & step(add, 2, 2).named("sum")).enqueue()
    for node in workflow_nodes(run.pk):
        execute(node)
    with pytest.raises(WorkflowError, match="ambiguous"):
        get_node_output(run.pk, "sum", database_alias=DB)
    assert get_node_output(run.pk, workflow_nodes(run.pk)[0].key, database_alias=DB) == 2


def test_workflow_expiry_preview_and_audit_redaction():
    run = Workflow(step(add, 1, 1)).enqueue()
    cancel_workflow(
        run.pk,
        request_id="retained",
        reason="sensitive reason",
        actor_reference="private actor",
        database_alias=DB,
    )
    action = WorkflowAction.objects.using(DB).get(run=run)
    intent = action.intent_hash
    WorkflowRun.objects.using(DB).filter(pk=run.pk).update(
        finished_at=timezone.now() - timedelta(days=31)
    )
    assert expire_workflows(database_alias=DB, dry_run=True) == 1
    assert find_workflow_run(run.pk).details_expired_at is None
    assert expire_workflows(database_alias=DB) == 1
    action.refresh_from_db(using=DB)
    assert not action.reason and not action.actor_reference
    assert action.intent_hash == intent
    assert action.request_id == "retained"
    repeated = cancel_workflow(
        run.pk,
        request_id="retained",
        reason="sensitive reason",
        actor_reference="private actor",
        database_alias=DB,
    )
    assert repeated.repeated


def test_deadline_callback_defers_network_publication(monkeypatch):
    import time

    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    complete(workflow_nodes(run.pk)[0], result=2)
    calls = []
    monkeypatch.setattr(dispatch, "publish_task", lambda *a, **kw: calls.append(a))
    advance_workflow(run.pk, database_alias=DB, deadline=time.monotonic() + 60)
    assert workflow_nodes(run.pk)[1].task_id is not None
    assert calls == []


def test_cancel_and_claim_serialize_on_independent_connections():
    from threading import Barrier

    run = Workflow(step(add, 1, 1) & step(add, 2, 2)).enqueue()
    first, _ = workflow_nodes(run.pk)
    ready = Barrier(2)

    def claim():
        connections.close_all()
        try:
            ready.wait(timeout=5)
            return claims.claim_task(
                task_id=first.task_id,
                queue_name="short",
                worker_name="short",
                lease_seconds=90,
                database_alias=DB,
            )
        finally:
            connections.close_all()

    def cancel():
        connections.close_all()
        try:
            ready.wait(timeout=5)
            return cancel_workflow(
                run.pk,
                request_id="race-cancel",
                reason="stop",
                actor_reference="tester",
                database_alias=DB,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_future = pool.submit(claim)
        cancelled_future = pool.submit(cancel)
        claimed = claimed_future.result(timeout=10)
        cancelled = cancelled_future.result(timeout=10)
    if claimed is None:
        assert cancelled.settled
    else:
        assert not cancelled.settled
        record = TaskRecord.objects.using(DB).get(pk=first.task_id)
        assert record.lease_token == claimed.attempt_id
        execution.execute_claimed_task(
            claimed=claimed,
            record=record,
            policy=default_policy(),
            allowed_modules=("tests.demo_tasks",),
            database_alias=DB,
        )
    advance_workflow(run.pk, database_alias=DB)
    assert find_workflow_run(run.pk).status == RunStatus.CANCELLED
    assert WorkflowAction.objects.using(DB).filter(run=run).count() == 1


@pytest.mark.parametrize("outcome", ["retry", "hold", "failure"])
def test_cancellation_settles_after_owned_attempt_outcomes(outcome):
    from django_tasks_aws.services import outcomes

    run = Workflow(step(add, 1, 1) & step(add, 2, 2)).enqueue()
    first, _ = workflow_nodes(run.pk)
    claimed = claims.claim_task(
        task_id=first.task_id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias=DB,
    )
    assert claimed is not None
    cancel_workflow(
        run.pk, request_id="outcomes", reason="stop", actor_reference="tester", database_alias=DB
    )
    kwargs = dict(task_id=str(first.task_id), attempt_id=claimed.attempt_id, database_alias=DB)
    match outcome:
        case "retry":
            assert outcomes.finalize_retry(
                **kwargs, retry_at=timezone.now() + timedelta(seconds=30)
            )
        case "hold":
            assert outcomes.finalize_hold(**kwargs, hold_reason=HoldReason.AMBIGUOUS_OUTCOME)
        case "failure":
            assert outcomes.finalize_failure(**kwargs, error={"error_type": "permanent"})
    advance_workflow(run.pk, database_alias=DB)
    assert find_workflow_run(run.pk).status == RunStatus.CANCELLED
