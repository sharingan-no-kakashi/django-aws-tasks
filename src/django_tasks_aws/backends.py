"""The SQL-backed Django Tasks backend.

The backend adapts Django's public ``django.tasks`` contract to the durable
shared SQL record model. Producers insert pending work inside their business
transaction and register an ``on_commit`` dispatch callback; workers and the
sweeper operate on the same records.
"""

import os
from typing import Any

from django.tasks import Task, TaskResult
from django.tasks.backends.base import BaseTaskBackend
from django.tasks.exceptions import TaskResultDoesNotExist

import django_tasks_aws
from django_tasks_aws import policies
from django_tasks_aws.configuration import (
    get_result_retention,
    load_worker_routes,
    validate_backend,
)
from django_tasks_aws.import_paths import resolve_task
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.results import get_task_result
from django_tasks_aws.schemas import RouteManifest
from django_tasks_aws.services import enqueue as enqueue_service
from django_tasks_aws.services.inspection import get_database_time

DEFAULT_MAX_ATTEMPTS = 5


class DatabaseSqsBackend(BaseTaskBackend):
    """Durable task backend: SQL for state, SQS for delivery signals."""

    supports_defer = True
    supports_get_result = True
    supports_priority = False
    supports_async_task = False

    def __init__(self, alias: str, params: dict[str, Any]) -> None:
        super().__init__(alias, params)
        self.database_alias = self.options.get("database_alias", "default")
        self.allowed_task_modules = tuple(self.options.get("allowed_task_modules", ()))
        self.routes: RouteManifest = load_worker_routes(self.options)
        self.policies = policies.build_policy_catalog(
            max_attempts=self.options.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
            allowed_routes=tuple(self.routes),
            overrides=self.options.get("policies"),
        )
        self.paused_routes = frozenset(name for name, route in self.routes.items() if route.paused)
        self.result_retention = get_result_retention(self.options)
        self.immediate_dispatch = self.options.get("immediate_dispatch", True)
        self.producer_version = self.options.get(
            "producer_version",
            os.environ.get("TASK_APPLICATION_VERSION", django_tasks_aws.__version__),
        )
        validate_backend(self)

    def enqueue(  # type: ignore[override]
        self,
        task: Task,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> TaskResult:
        outcome = enqueue_service.enqueue(self, task=task, args=args, kwargs=kwargs)
        return get_task_result(task, outcome.record)

    def get_result(self, result_id: str) -> TaskResult:
        record = (
            TaskRecord.objects.using(self.database_alias)
            .filter(pk=result_id, backend_alias=self.alias)
            .first()
        )
        if (
            record is None
            or record.details_expired_at
            or (
                record.finished_at
                and record.finished_at
                < get_database_time(self.database_alias) - self.result_retention
            )
        ):
            raise TaskResultDoesNotExist(f"Task result {result_id!r} does not exist.")
        task = resolve_task(record.task_name, allowed_modules=self.allowed_task_modules)
        task = task.using(backend=record.backend_alias, queue_name=record.queue_name)
        return get_task_result(task, record)
