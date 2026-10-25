"""AWS task backend adapter and optional lifecycle integration."""

from dataclasses import replace
from inspect import signature

from django.db.models import Q, Subquery
from django.tasks import Task, task_backends
from django.tasks.exceptions import InvalidTask
from django.utils.dateparse import parse_datetime

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.exceptions import DjangoTasksAWSError
from django_tasks_aws.import_paths import resolve_task
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.services import enqueue as enqueue_service

from .adapter import TaskSnapshot
from .exceptions import PreparationError, WorkflowError
from .models import NodeStatus, WorkflowAction, WorkflowNode


class AwsTaskAdapter:
    def __init__(self, backend_alias):
        backend = task_backends[backend_alias]
        if not isinstance(backend, DatabaseSqsBackend):
            raise WorkflowError("The AWS workflow adapter requires DatabaseSqsBackend")
        self.backend = backend
        self.database_alias = self.backend.database_alias

    def describe(self, task, args, kwargs):
        if not isinstance(task, Task):
            raise WorkflowError("step() requires an existing Django Task")
        try:
            enqueue_service.validate_task(self.backend, task)
        except (InvalidTask, DjangoTasksAWSError, ValueError) as error:
            raise WorkflowError(
                f"Cannot prepare workflow step {task.module_path!r}: {error}"
            ) from error
        bind_arguments(task, args, kwargs, WorkflowError)
        return {
            "path": task.module_path,
            "queue_name": task.queue_name,
            "priority": task.priority,
            "run_after": task.run_after.isoformat() if task.run_after is not None else None,
            "takes_context": task.takes_context,
        }

    def enqueue(self, node, args, kwargs):
        try:
            task = self.restore_task(node.task_config)
            bind_arguments(task, args, kwargs, PreparationError)
            outcome = enqueue_service.enqueue(
                self.backend,
                task=task,
                args=tuple(args),
                kwargs=kwargs,
                submission_key=node.key,
                dedupe_scope=f"workflow:{node.run_id}",
            )
        except (
            DjangoTasksAWSError,
            InvalidTask,
            ImportError,
            AttributeError,
            ValueError,
        ) as error:
            raise PreparationError("Saved task or resolved arguments cannot be enqueued") from error
        return str(outcome.record.id)

    def retry(self, node, args, kwargs, *, request_id):
        """Create a replacement task linked to the original, idempotent per request."""
        try:
            task = self.restore_task(node.task_config)
            bind_arguments(task, args, kwargs, PreparationError)
            outcome = enqueue_service.enqueue(
                self.backend,
                task=task,
                args=tuple(args),
                kwargs=kwargs,
                submission_key=request_id,
                operation_key=self.get_original_operation_key(node),
                dedupe_scope=f"workflow:{node.run_id}",
                replay_of_id=node.task_id,
            )
        except (
            DjangoTasksAWSError,
            InvalidTask,
            ImportError,
            AttributeError,
            ValueError,
        ) as error:
            raise PreparationError("Saved task or resolved arguments cannot be retried") from error
        return str(outcome.record.id)

    def get_original_operation_key(self, node):
        if node.task_id is None:
            return None
        record = TaskRecord.objects.using(self.database_alias).filter(pk=node.task_id).first()
        return record.operation_key if record is not None else None

    def is_result_available(self, task_id):
        record = TaskRecord.objects.using(self.database_alias).filter(pk=task_id).first()
        return record is not None and record.details_expired_at is None

    def is_failure_uncertain(self, task_id):
        record = TaskRecord.objects.using(self.database_alias).filter(pk=task_id).first()
        if record is None:
            return False
        return bool((record.last_error or {}).get("uncertain"))

    def hold(self, task_id):
        from django_tasks_aws.models import HoldReason
        from django_tasks_aws.services import outcomes

        return outcomes.hold_pending(
            task_id=str(task_id),
            hold_reason=HoldReason.CANCELLED,
            database_alias=self.database_alias,
        )

    def restore_task(self, config):
        task = resolve_task(config["path"], allowed_modules=self.backend.allowed_task_modules)
        if task.takes_context != config["takes_context"]:
            raise PreparationError("Task context contract changed after workflow submission")
        run_after = parse_datetime(config["run_after"]) if config["run_after"] else None
        task = task.using(
            backend=self.backend.alias, queue_name=config["queue_name"], priority=config["priority"]
        )
        # replace also clears a newly introduced decorator delay when the saved delay was None.
        return replace(task, run_after=run_after)

    def get_snapshots(self, task_ids):
        records = TaskRecord.objects.using(self.database_alias).filter(
            id__in=task_ids, backend_alias=self.backend.alias
        )
        return {str(record.pk): build_task_snapshot(record) for record in records}


def bind_arguments(task, args, kwargs, error_type):
    context = (object(),) if task.takes_context else ()
    try:
        signature(task.func).bind(*context, *args, **kwargs)
    except TypeError as error:
        raise error_type(f"Invalid arguments for {task.module_path}") from error


def build_task_snapshot(record):
    if record.status == "PENDING":
        status = NodeStatus.BLOCKED if record.held_at is not None else NodeStatus.READY
    else:
        status = record.status
    return TaskSnapshot(status, record.result, record.finished_at)


def protect_workflow_results(sender, *, records, **kwargs):
    needed = WorkflowNode.objects.using(records.db).filter(
        run__details_expired_at__isnull=True, task_id__isnull=False
    )
    history = WorkflowAction.objects.using(records.db).filter(run__details_expired_at__isnull=True)
    return records.exclude(
        Q(pk__in=Subquery(needed.values("task_id")))
        | Q(pk__in=Subquery(history.values("original_task_id")))
        | Q(pk__in=Subquery(history.values("replacement_task_id")))
    )


def advance_after_task(sender, *, task_id, database_alias, deadline=None, **kwargs):
    from .services import advance_workflow

    node = WorkflowNode.objects.using(database_alias).filter(task_id=task_id).first()
    if node is not None:
        advance_workflow(node.run_id, database_alias=database_alias, deadline=deadline)


def reconcile_after_tasks(sender, *, database_alias, backend_alias, deadline=None, **kwargs):
    from .services import reconcile_workflows

    reconcile_workflows(
        database_alias=database_alias, backend_alias=backend_alias, deadline=deadline
    )
