"""Configuration shared by producers and both invocation adapters."""

import os
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Self, cast

from django.db import connections
from django.tasks import task_backends
from pydantic import BaseModel, ConfigDict, ValidationError

from django_tasks_aws.databases import get_database_adapter
from django_tasks_aws.exceptions import ConfigurationError

if TYPE_CHECKING:
    from django_tasks_aws.schemas import RouteManifest


class ConfigurationInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    @classmethod
    def load(cls, raw: object, *, label: str) -> Self:
        try:
            return cls.model_validate(raw)
        except ValidationError as error:
            raise ConfigurationError(f"invalid {label}: {error}") from error


def load_worker_routes(options: dict[str, Any]) -> "RouteManifest":
    from django_tasks_aws.schemas import load_route_manifest

    routes = load_route_manifest(options.get("workers", {}))
    paused = frozenset(options.get("paused_routes", ()))
    if paused - routes.keys():
        raise ValueError("paused_routes contains an unknown route")
    return {
        name: replace(route, paused=route.paused or name in paused)
        for name, route in routes.items()
    }


def get_result_retention(options: dict[str, Any]) -> timedelta:
    days = options.get("result_retention_days", 30)
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError("result_retention_days must be a positive integer")
    return timedelta(days=days)


def validate_backend(backend):
    if set(backend.routes) != backend.queues:
        raise ConfigurationError("QUEUES must match the named worker routes")
    for policy in (backend.policies.default, *backend.policies.overrides.values()):
        unknown = set(policy.allowed_routes) - backend.routes.keys()
        if unknown:
            raise ConfigurationError(f"Policy {policy.task_path!r} uses unknown routes: {unknown}")
    if not backend.allowed_task_modules or not all(
        isinstance(module, str) and module.strip() for module in backend.allowed_task_modules
    ):
        raise ConfigurationError("allowed_task_modules must explicitly allow application modules")
    if backend.database_alias not in connections:
        raise ConfigurationError("database_alias does not name a configured database")
    get_database_adapter(backend.database_alias)


def require_writer(database_alias):
    get_database_adapter(database_alias).require_writer(connections[database_alias])


def get_invocation_backend():
    from django_tasks_aws.backends import DatabaseSqsBackend

    alias = os.environ.get("TASK_BACKEND_ALIAS", "default")
    try:
        backend = cast(DatabaseSqsBackend, task_backends[alias])
    except KeyError as error:
        raise ConfigurationError(f"Unknown TASK_BACKEND_ALIAS {alias!r}") from error
    validate_backend(backend)
    resolver = backend.options.get("credentials_resolver")
    if resolver:
        from django.utils.module_loading import import_string

        import_string(resolver)(backend.database_alias)
    require_writer(backend.database_alias)
    return backend
