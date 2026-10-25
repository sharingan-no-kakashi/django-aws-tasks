"""Task-engine boundary; orchestration only depends on this small contract."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from django.apps import apps
from django.conf import settings
from django.utils.module_loading import import_string

AWS_ADAPTER = "django_tasks_aws.workflows.aws.AwsTaskAdapter"


@dataclass(frozen=True)
class TaskSnapshot:
    status: str
    result: Any = None
    finished_at: datetime | None = None


class TaskAdapter(Protocol):
    database_alias: str

    def describe(self, task, args, kwargs) -> dict: ...

    def enqueue(self, node, args, kwargs) -> str: ...

    def get_snapshots(self, task_ids) -> dict[str, TaskSnapshot]: ...

    def retry(self, node, args, kwargs, *, request_id) -> str: ...

    def is_result_available(self, task_id) -> bool: ...

    def is_failure_uncertain(self, task_id) -> bool: ...

    def hold(self, task_id) -> bool: ...


def get_task_adapter(backend_alias) -> TaskAdapter:
    path = getattr(
        settings,
        "DJANGO_TASKS_WORKFLOW_ADAPTER",
        AWS_ADAPTER,
    )
    return import_string(path)(backend_alias)


def uses_aws_workflows() -> bool:
    return (
        apps.is_installed("django_tasks_aws.workflows")
        and getattr(settings, "DJANGO_TASKS_WORKFLOW_ADAPTER", AWS_ADAPTER) == AWS_ADAPTER
    )


def get_workflow_fields(task_id, database_alias):
    if not apps.is_installed("django_tasks_aws.workflows"):
        return {}
    from django_tasks_aws.workflows.models import WorkflowNode

    node = (
        WorkflowNode.objects.using(database_alias)
        .filter(task_id=task_id)
        .values("run_id", "key")
        .first()
    )
    return {"workflow_id": str(node["run_id"]), "step_key": node["key"]} if node else {}
