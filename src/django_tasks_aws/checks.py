"""Django system checks for backend and worker configuration.

These run at startup so unsafe or incomplete configuration fails loudly rather
than being discovered when a worker claims work under the wrong lease.
"""

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.schemas import load_route_manifest

BACKEND_PATHS = {
    "django_tasks_aws.backends.DatabaseSqsBackend",
}


@register()
def check_worker_configuration(app_configs, **kwargs) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    tasks = getattr(settings, "TASKS", {})
    for alias, backend_config in tasks.items():
        if backend_config.get("BACKEND") not in BACKEND_PATHS:
            continue
        messages.extend(check_alias(alias, backend_config))
    return messages


def check_alias(alias: str, backend_config: dict) -> list[CheckMessage]:
    options = backend_config.get("OPTIONS", {})
    raw_workers = options.get("workers")
    if not raw_workers:
        return [
            Error(
                "No route manifest configured.",
                hint="Set OPTIONS['workers']",
                id="django_tasks_aws.E001",
                obj=alias,
            )
        ]

    try:
        manifest = load_route_manifest(raw_workers)
    except ConfigurationError as error:
        return [Error(str(error), id="django_tasks_aws.E002", obj=alias)]

    messages: list[CheckMessage] = []
    queues = set(backend_config.get("QUEUES", []))
    workers = set(manifest)
    missing = queues - workers
    extra = workers - queues
    if missing:
        messages.append(
            Error(
                f"QUEUES names have no worker profile: {sorted(missing)}",
                id="django_tasks_aws.E003",
                obj=alias,
            )
        )
    if extra:
        messages.append(
            Error(
                f"Worker profiles are not listed in QUEUES: {sorted(extra)}",
                id="django_tasks_aws.E004",
                obj=alias,
            )
        )
    if not messages:
        try:
            from django.utils.module_loading import import_string

            import_string(backend_config["BACKEND"])(alias, backend_config)
        except (ConfigurationError, ValueError, TypeError, AttributeError) as error:
            messages.append(Error(str(error), id="django_tasks_aws.E005", obj=alias))
    return messages
