"""Import-path identity and resolution.

A task's durable identity is its fully-qualified Python import path, e.g.
``sample_app.tasks.record_event``. The worker resolves that path from its own
deployed application code and must find a module-level Django ``Task`` inside a
configured allowlist of modules.
"""

from django.tasks import Task
from django.utils.module_loading import import_string

from django_tasks_aws.exceptions import TaskResolutionError


def resolve_task(task_path: str, *, allowed_modules: tuple[str, ...] = ()) -> Task:
    """Import and validate the stored task path back to a supported Task."""
    validate_allowed_import_path(task_path, allowed_modules)
    try:
        candidate = import_string(task_path)
    except (ImportError, AttributeError) as error:
        raise TaskResolutionError(task_path) from error
    if not isinstance(candidate, Task):
        raise TaskResolutionError(f"{task_path!r} does not resolve to a Task")
    return candidate


def validate_allowed_import_path(task_path: str, allowed_modules: tuple[str, ...]) -> None:
    if not allowed_modules:
        return
    module = task_path.rsplit(".", 1)[0]
    allowed = any(module == prefix or module.startswith(prefix + ".") for prefix in allowed_modules)
    if not allowed:
        raise TaskResolutionError(f"{task_path!r} is outside the allowed task modules")


def reconstruct_record_task(record, *, task: Task) -> Task:
    """Bind a resolved Task to the record's persisted backend and route.

    This never mutates the imported global Task; it returns a per-record copy so
    today's decorator defaults cannot overwrite the producer's selection.
    """
    validate_context_contract(record, task)
    return task.using(backend=record.backend_alias, queue_name=record.queue_name)


def validate_context_contract(record, task: Task) -> None:
    options = getattr(record, "task_options", {})
    # Older development records have no saved context contract.
    if options == {}:
        return
    if not isinstance(options, dict):
        raise TaskResolutionError("Saved task options must be a mapping")
    takes_context = options.get("takes_context")
    if type(takes_context) is not bool or takes_context != task.takes_context:
        raise TaskResolutionError("Task context contract changed after submission")
