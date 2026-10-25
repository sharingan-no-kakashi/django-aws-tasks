"""Workflow submission identity uses NULL-agnostic uniqueness on every engine."""

import pytest

from django_tasks_aws.databases.constraints import SubmissionKeyConstraint
from django_tasks_aws.workflows import Workflow, parallel, step
from django_tasks_aws.workflows.exceptions import WorkflowError
from django_tasks_aws.workflows.models import WorkflowRun


def test_workflow_submission_uses_submission_key_constraint():
    constraint = next(
        c for c in WorkflowRun._meta.constraints if c.name == "workflow_submission_unique"
    )
    assert isinstance(constraint, SubmissionKeyConstraint)


def stub(*args, **kwargs):
    return object()


def test_chain_retains_every_node():
    a = step(stub, 1)
    b = step(stub, 2)
    c = step(stub, 3)

    graph = Workflow(a >> b >> c).graph

    assert list(graph.nodes) == [a, b, c]
    assert {e for e in graph.edges} == {(a.key, b.key), (b.key, c.key)}


def test_parallel_merges_branches_without_edges():
    a = step(stub, 1)
    b = step(stub, 2)

    graph = parallel(a, b)

    assert set(graph.nodes) == {a, b}
    assert graph.edges == frozenset()


def test_fan_out_fan_in_links_exits_to_entries():
    a = step(stub, 1)
    s2 = step(stub, 2)
    s3 = step(stub, 3)
    s4 = step(stub, 4)

    graph = Workflow(a >> parallel(s2, s3) >> s4).graph

    assert graph.edges == frozenset(
        {(a.key, s2.key), (a.key, s3.key), (s2.key, s4.key), (s3.key, s4.key)}
    )


def test_truth_testing_graph_raises_type_error():
    with pytest.raises(TypeError):
        bool(step(stub, 1))


def test_output_selector_rejects_unsupported_keys():
    node = step(stub, 1)

    with pytest.raises(WorkflowError):
        node.output[1.5]


def test_parallel_group_cannot_be_empty():
    with pytest.raises(WorkflowError):
        parallel()


def test_named_step_preserves_identity_and_adds_label():
    node = step(stub, 1)
    labelled = node.named("increment")

    assert labelled.key == node.key
    assert labelled.name == "increment"
    assert node.name is None


def test_two_steps_of_same_task_have_distinct_names():
    first = step(stub, 1).named("first")
    second = step(stub, 2).named("second")

    assert first.name != second.name


def test_step_kwargs_are_not_reserved_for_metadata():
    node = step(stub, name="task-argument")

    assert node.kwargs == {"name": "task-argument"}
    assert node.name is None
