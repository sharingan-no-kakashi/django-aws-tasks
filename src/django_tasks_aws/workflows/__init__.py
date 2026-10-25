"""Optional, durable DAG orchestration over existing Django tasks."""

from .graph import Workflow, parallel, step

__all__ = ["Workflow", "parallel", "step", "get_run_status", "get_node_output", "RunSnapshot"]

_INSPECTION = {"get_run_status", "get_node_output", "RunSnapshot"}


def __getattr__(name):
    if name in _INSPECTION:
        from . import inspection

        return getattr(inspection, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
