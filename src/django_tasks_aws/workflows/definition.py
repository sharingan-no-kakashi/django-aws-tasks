"""Validate and snapshot graph definitions before any durable acceptance."""

import hashlib
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter

from django.apps import apps

from .adapter import get_task_adapter
from .configuration import get_workflow_limits
from .exceptions import WorkflowError
from .values import encode_value, get_output_references, serialize_canonical_json


@dataclass(frozen=True)
class Definition:
    backend_alias: str
    database_alias: str
    nodes: tuple[dict, ...]
    names: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    digest: str


def compile_graph(graph) -> Definition:
    if not apps.is_installed("django_tasks_aws.workflows"):
        raise WorkflowError("Add django_tasks_aws.workflows to INSTALLED_APPS and migrate first")
    nodes, edges = collect_nodes(graph)
    backend_alias = get_shared_backend(nodes)
    adapter = get_task_adapter(backend_alias)
    keys = {node.key: str(index) for index, node in enumerate(nodes)}
    if len(keys) != len(nodes):
        raise WorkflowError("Distinct workflow nodes cannot share an identity")
    validate_dependencies(keys, edges)
    saved = tuple(build_node_definition(node, keys, adapter) for node in nodes)
    names = tuple(node.name or "" for node in nodes)
    links = tuple(sorted((keys[parent], keys[child]) for parent, child in edges))
    encoded = serialize_canonical_json({"version": 1, "nodes": saved, "edges": links})
    if len(encoded.encode()) > get_workflow_limits().max_definition_bytes:
        raise WorkflowError("Workflow definition exceeds max_definition_bytes")
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return Definition(backend_alias, adapter.database_alias, saved, names, links, digest)


def collect_nodes(graph):
    limits = get_workflow_limits()
    nodes = dict.fromkeys(graph.nodes)
    pending = list(nodes)
    edges = set(graph.edges)
    while pending:
        node = pending.pop()
        for reference in get_output_references([list(node.args), node.kwargs]):
            edges.add((reference.node.key, node.key))
            if reference.node not in nodes:
                nodes[reference.node] = None
                pending.append(reference.node)
        if len(nodes) > limits.max_nodes or len(edges) > limits.max_edges:
            raise WorkflowError("Workflow exceeds max_nodes or max_edges")
    return tuple(nodes), edges


def get_shared_backend(nodes):
    aliases = {getattr(node.task, "backend", None) for node in nodes}
    if len(aliases) != 1 or None in aliases:
        raise WorkflowError("All workflow nodes must use tasks from one backend")
    return aliases.pop()


def build_node_definition(node, keys, adapter):
    return {
        "key": keys[node.key],
        "task_config": adapter.describe(node.task, node.args, node.kwargs),
        "arguments": encode_value({"args": list(node.args), "kwargs": node.kwargs}, keys),
    }


def validate_dependencies(keys, edges):
    parents: dict[str, set[str]] = {key: set() for key in keys}
    for parent, child in edges:
        if parent not in parents or child not in parents:
            raise WorkflowError("Workflow dependency refers to a missing node")
        parents[child].add(parent)
    try:
        tuple(TopologicalSorter(parents).static_order())
    except CycleError as error:
        raise WorkflowError("Workflow dependencies contain a cycle") from error
