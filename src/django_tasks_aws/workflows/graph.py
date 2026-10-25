"""Pure graph composition; constructing expressions never enqueues tasks."""

from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from .exceptions import WorkflowError


class Expression:
    def __rshift__(self, other):
        left, right = as_graph(self), as_graph(other)
        edges = frozenset((a, b) for a in left.exits for b in right.entries)
        return Graph(merge(left.nodes, right.nodes), left.edges | right.edges | edges)

    def __and__(self, other):
        left, right = as_graph(self), as_graph(other)
        return Graph(merge(left.nodes, right.nodes), left.edges | right.edges)

    def __bool__(self):
        raise TypeError("Use >> for dependencies and & for parallel workflow branches")


@dataclass(frozen=True, eq=False)
class Node(Expression):
    task: Any
    args: tuple
    kwargs: dict
    key: str = field(default_factory=lambda: uuid4().hex)
    name: str | None = None

    @property
    def output(self):
        return Output(self)

    def named(self, name):
        """Return this node with a display name, without changing its identity."""
        if not isinstance(name, str) or not name.strip():
            raise WorkflowError("A step display name must be a nonempty string")
        return replace(self, name=name)


@dataclass(frozen=True)
class Output:
    node: Node
    path: tuple[str | int, ...] = ()

    def __getitem__(self, key):
        if isinstance(key, bool) or not isinstance(key, (str, int)):
            raise WorkflowError("Output selectors must be string keys or integer indexes")
        return Output(self.node, (*self.path, key))


@dataclass(frozen=True, eq=False)
class Graph(Expression):
    nodes: tuple[Node, ...]
    edges: frozenset[tuple[str, str]] = frozenset()

    @property
    def entries(self):
        children = {child for _, child in self.edges}
        return {node.key for node in self.nodes} - children

    @property
    def exits(self):
        parents = {parent for parent, _ in self.edges}
        return {node.key for node in self.nodes} - parents


def merge(left, right):
    return tuple(dict.fromkeys((*left, *right)))


def as_graph(expression):
    if isinstance(expression, Node):
        return Graph((expression,))
    if isinstance(expression, Graph):
        return expression
    raise TypeError("Workflow expressions must be step() nodes or graph expressions")


def step(task, *args, **kwargs) -> Node:
    """Wrap an existing task without invoking it; every call is a distinct node."""
    return Node(task, args, kwargs)


def parallel(*expressions) -> Graph:
    if not expressions:
        raise WorkflowError("A parallel group cannot be empty")
    graph = as_graph(expressions[0])
    for expression in expressions[1:]:
        graph = graph & expression
    return graph


class Workflow:
    def __init__(self, *expressions, name=None, business_reference=None):
        self.name = name
        self.business_reference = business_reference
        self.graph = parallel(*expressions)

    def enqueue(self, *, submission_key=None, name=None, business_reference=None):
        from django.apps import apps

        if not apps.is_installed("django_tasks_aws.workflows"):
            raise WorkflowError(
                "Add django_tasks_aws.workflows to INSTALLED_APPS and migrate first"
            )
        from .services import submit_workflow

        return submit_workflow(
            self.graph,
            submission_key=submission_key,
            name=name if name is not None else self.name,
            business_reference=(
                business_reference if business_reference is not None else self.business_reference
            ),
        )
