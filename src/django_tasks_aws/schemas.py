"""Typed configuration and protocol records.

These records sit at the boundary between Django settings (dynamic,
untyped input) and the domain code (validated, typed values). Configuration is
loaded and validated here, then passed into services as immutable records.
"""

from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlparse

from pydantic import Field

from django_tasks_aws.configuration import ConfigurationInput
from django_tasks_aws.exceptions import ConfigurationError

# A producer-selected route name maps to one named worker's resolved resources.
RouteManifest = dict[str, "WorkerConfig"]


class _WorkerProfile(ConfigurationInput):
    source_queue_arn: str = Field(min_length=1)
    source_queue_url: str = Field(min_length=1)
    timeout_seconds: int
    lease_seconds: int
    visibility_timeout_seconds: int
    max_receive_count: int
    concurrency: int
    dispatch_limit: int
    redispatch_seconds: int
    paused: bool = False


@dataclass(frozen=True)
class WorkerConfig:
    """A validated named-worker profile resolved from the route manifest."""

    name: str
    source_queue_arn: str
    source_queue_url: str
    timeout_seconds: int
    lease_seconds: int
    visibility_timeout_seconds: int
    max_receive_count: int
    concurrency: int
    dispatch_limit: int
    redispatch_seconds: int
    paused: bool = False

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Self:
        if not isinstance(name, str) or not name or not isinstance(data, dict):
            raise ConfigurationError("Each worker requires a non-empty name and profile mapping")
        profile = _WorkerProfile.load(data, label=f"worker profile {name!r}")
        config = cls(name=name, **profile.model_dump())
        problems = config.find_problems()
        if problems:
            raise ConfigurationError(f"invalid worker profile {name!r}: " + "; ".join(problems))
        return config

    def find_problems(self) -> list[str]:
        """Return human-readable validation problems (empty when valid)."""
        problems: list[str] = []
        if not isinstance(self.paused, bool):
            problems.append("paused must be boolean")
        if not self.name:
            problems.append("name is empty")
        if not self.source_queue_arn or not self.source_queue_url:
            problems.append("source queue identity is missing")
        if self.timeout_seconds <= 5:
            problems.append("timeout must exceed the five-second finalization allowance")
        if self.lease_seconds < self.timeout_seconds + 5:
            problems.append("lease must exceed the worker timeout by at least five seconds")
        if self.visibility_timeout_seconds < 6 * self.timeout_seconds:
            problems.append("visibility timeout must be at least 6x the worker timeout")
        if not 5 <= self.max_receive_count <= 1000:
            problems.append("max_receive_count must be between 5 and 1000")
        if self.concurrency < 1:
            problems.append("concurrency must be positive")
        if self.dispatch_limit < 1:
            problems.append("dispatch_limit must be positive")
        if self.redispatch_seconds < 0:
            problems.append("redispatch_seconds must not be negative")
        return problems


@dataclass(frozen=True)
class Envelope:
    """The versioned queue payload: a delivery hint, not execution permission."""

    schema_version: int
    task_id: str


@dataclass(frozen=True)
class WorkerIdentity:
    """A worker's trusted name and expected source-queue ARN."""

    name: str
    source_arn: str


def load_route_manifest(raw: Any) -> RouteManifest:
    """Parse and validate the Terraform-injected route manifest.

    ``raw`` is the backend ``workers`` option: a mapping of producer-selected
    route name to worker profile. A missing or malformed manifest raises
    ``ConfigurationError`` rather than silently disabling routing.
    """
    if not isinstance(raw, dict):
        raise ConfigurationError("route manifest must be a mapping of route name to profile")
    if not raw:
        raise ConfigurationError("route manifest must not be empty")
    routes = {name: WorkerConfig.from_dict(name, data) for name, data in raw.items()}
    for field in ("source_queue_arn", "source_queue_url"):
        identities = [getattr(route, field) for route in routes.values()]
        if len(set(identities)) != len(identities):
            raise ConfigurationError(f"Worker routes must have distinct {field} values")
    for route in routes.values():
        arn = route.source_queue_arn.split(":")
        url = urlparse(route.source_queue_url)
        if len(arn) != 6 or arn[0] != "arn" or arn[2] != "sqs":
            raise ConfigurationError("source_queue_arn must identify a standard SQS queue")
        if arn[-1].endswith(".fifo") or url.scheme != "https":
            raise ConfigurationError("Standard SQS queues with HTTPS URLs are required")
        if url.path.strip("/") != f"{arn[4]}/{arn[5]}":
            raise ConfigurationError("Source queue ARN and URL account/name disagree")
    return routes
