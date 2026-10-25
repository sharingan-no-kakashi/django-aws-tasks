"""Isolated transports and bounded synchronous lifecycle assertions for consumers."""

import time
from dataclasses import dataclass, field

from django.db import connections

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.configuration import require_writer
from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services import reconcile
from django_tasks_aws.services.execution import ExecutionBudget
from django_tasks_aws.services.inspection import get_task_detail
from django_tasks_aws.services.worker import handle_task


@dataclass
class RecordingTransport:
    """A per-instance transport; never patches the global transport or sleeps."""

    messages: list[tuple[str, str]] = field(default_factory=list)

    def send_message(self, queue_url: str, body: str) -> str:
        self.messages.append((queue_url, body))
        return f"recorded-{len(self.messages)}"


@dataclass(frozen=True)
class TaskHarness:
    backend: DatabaseSqsBackend

    def __post_init__(self):
        if not isinstance(self.backend, DatabaseSqsBackend) or self.backend.immediate_dispatch:
            raise ConfigurationError(
                "TaskHarness requires DatabaseSqsBackend with immediate_dispatch=False"
            )
        require_writer(self.backend.database_alias)

    def run_pending(self, *, queue=None, limit=20, seconds=30):
        self.require_committed()
        if type(limit) is not int or not 1 <= limit <= 200 or not 6 <= seconds <= 300:
            raise ValueError("Limit must be 1–200 and seconds must be 6–300")
        deadline = time.monotonic() + seconds
        decisions: list[tuple[str, object]] = []
        for route in self.backend.routes.values():
            if route.paused or (queue is not None and route.name != queue):
                continue
            decisions.extend(self.run_route(route, limit - len(decisions), deadline))
        return tuple(decisions)

    def run_route(self, route, limit, deadline):
        identities = (
            reconcile.select_due_task_ids(
                database_alias=self.backend.database_alias,
                backend_alias=self.backend.alias,
                queue_name=route.name,
                dispatch_limit=route.dispatch_limit,
                limit=limit,
            )
            if limit
            else []
        )
        decisions = []
        for identity in identities:
            if deadline - time.monotonic() <= 5:
                break
            seconds = min(route.timeout_seconds, deadline - time.monotonic())
            result = handle_task(
                identity,
                backend=self.backend,
                worker=WorkerIdentity(route.name, route.source_queue_arn),
                remaining_seconds=seconds,
                lease_seconds=route.lease_seconds,
                budget=ExecutionBudget.after(seconds),
            )
            decisions.append((str(identity), result))
        return decisions

    def reconcile(self, *, limit=100, seconds=30):
        self.require_committed()
        if type(limit) is not int or not 1 <= limit <= 200 or not 6 <= seconds <= 60:
            raise ValueError("Limit must be 1–200 and seconds must be 6–60")
        return reconcile.run_reconciliation(
            backend_alias=self.backend.alias,
            database_alias=self.backend.database_alias,
            routes=self.backend.routes,
            policy_catalog=self.backend.policies,
            transport=RecordingTransport(),
            dispatch=False,
            recover_limit=limit,
            per_queue_limit=limit,
            deadline=time.monotonic() + seconds,
        )

    def detail(self, task_id, *, sensitive=False):
        return get_task_detail(self.backend, str(task_id), sensitive=sensitive)

    def assert_status(self, task_id, expected, *, attempts=None, hold=None):
        record = TaskRecord.objects.using(self.backend.database_alias).get(
            pk=task_id, backend_alias=self.backend.alias
        )
        assert record.status == expected, (record.status, expected)
        if attempts is not None:
            assert record.attempts_started == attempts
        if hold is not None:
            assert record.hold_reason == hold
        return record

    def require_committed(self):
        if connections[self.backend.database_alias].in_atomic_block:
            raise ConfigurationError(
                "Commit acceptance before executing; use transaction=True tests"
            )
