"""Validated, externally configurable orchestration limits."""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class WorkflowLimits:
    max_nodes: int = 100
    max_edges: int = 1000
    max_definition_bytes: int = 1024 * 1024
    advancement_batch: int = 20
    sweep_limit: int = 100
    retention_days: int = 30
    minimum_remaining_seconds: int = 5
    stalled_seconds: int = 120


def get_workflow_limits() -> WorkflowLimits:
    values = getattr(settings, "DJANGO_TASKS_WORKFLOWS", {})
    limits = WorkflowLimits(**values)
    for name in limits.__dataclass_fields__:
        value = getattr(limits, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"Workflow setting {name} must be a positive integer")
    return limits
