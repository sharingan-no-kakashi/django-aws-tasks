"""Adapt durable task state and attempt history to Django's result API."""

from dataclasses import dataclass

from django.tasks import TaskResult, TaskResultStatus
from django.tasks.base import TASK_REFRESH_ATTRS, TaskError

from django_tasks_aws.models import TaskRecord, TaskStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class DurableTaskResult(TaskResult):  # type: ignore[misc]
    attempts_started: int

    @property
    def attempts(self) -> int:
        return self.attempts_started

    def refresh(self) -> None:
        refreshed = self.task.get_backend().get_result(self.id)
        self.update_from(refreshed)

    async def arefresh(self) -> None:
        refreshed = await self.task.get_backend().aget_result(self.id)
        self.update_from(refreshed)

    def update_from(self, refreshed) -> None:
        for attribute in TASK_REFRESH_ATTRS | {"attempts_started"}:
            object.__setattr__(self, attribute, getattr(refreshed, attribute))


def get_task_result(task, record: TaskRecord) -> DurableTaskResult:
    attempts = list(record.attempts.order_by("attempt_number"))
    status = TaskResultStatus.READY if record.status == TaskStatus.PENDING else record.status
    result = DurableTaskResult(
        task=task,
        id=str(record.id),
        status=TaskResultStatus(status),
        enqueued_at=record.created_at,
        started_at=record.first_started_at,
        finished_at=record.finished_at,
        last_attempted_at=record.last_attempted_at,
        args=list(record.payload.get("args", [])),
        kwargs=dict(record.payload.get("kwargs", {})),
        backend=record.backend_alias,
        errors=get_task_errors(attempts, record.last_error),
        worker_ids=[attempt.worker_name for attempt in attempts],
        attempts_started=record.attempts_started,
    )
    if record.status == TaskStatus.SUCCESSFUL:
        object.__setattr__(result, "_return_value", record.result)
    return result


def get_task_errors(attempts, last_error: dict | None) -> list[TaskError]:
    errors = [
        TaskError(
            exception_class_path=attempt.error_type,
            traceback=attempt.traceback or attempt.error_message or "",
        )
        for attempt in attempts
        if attempt.error_type
    ]
    if not errors and last_error:
        errors.append(
            TaskError(
                exception_class_path=last_error.get("error_type", "builtins.Exception"),
                traceback=last_error.get("message", ""),
            )
        )
    return errors
