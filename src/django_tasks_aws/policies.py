"""Task policies keyed by import path.

Policies are the package's declarative extension point: they describe allowed
routes, argument versions, retry classification, safe repetition, and ambiguity
behavior without creating an alternate logical task identity.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Any

from django.utils.module_loading import import_string
from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError

from django_tasks_aws.configuration import ConfigurationInput
from django_tasks_aws.exceptions import ConfigurationError

MAX_BACKOFF_SECONDS = 30 * 60


def normalize_sequence(value: Any) -> Any:
    """Accept JSON lists as tuples without coercing their elements or scalar strings."""
    return tuple(value) if isinstance(value, list) else value


_Names = Annotated[tuple[str, ...], BeforeValidator(normalize_sequence)]
_Versions = Annotated[tuple[int, ...], BeforeValidator(normalize_sequence)]


class _PolicyInput(ConfigurationInput):
    allowed_routes: _Names = Field(min_length=1)
    payload_versions: _Versions = (1,)
    max_attempts: int = Field(ge=1)
    retryable_exception_paths: _Names = ()
    permanent_exception_paths: _Names = ()
    retry_safe: bool = False
    ambiguous_retry: bool = False
    operation_key_resolver: str | None = None


_OVERRIDES = TypeAdapter(dict[str, dict[str, Any]])


def parse_policy_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    try:
        return _OVERRIDES.validate_python({} if raw is None else raw, strict=True)
    except ValidationError as error:
        raise ConfigurationError(
            "policies must map task import paths to profile mappings"
        ) from error


class RetryDecision(StrEnum):
    RETRY = "retry"
    FAIL = "fail"
    HOLD = "hold"


@dataclass(frozen=True)
class TaskPolicy:
    """A validated policy for one import path (or the default)."""

    task_path: str
    allowed_routes: tuple[str, ...]
    payload_versions: tuple[int, ...]
    max_attempts: int
    retryable_exception_paths: tuple[str, ...] = ()
    permanent_exception_paths: tuple[str, ...] = ()
    retry_safe: bool = False
    ambiguous_retry: bool = False
    operation_key_resolver: str | None = None

    def __post_init__(self) -> None:
        problems = self.find_problems()
        if problems:
            raise ConfigurationError(f"invalid policy {self.task_path!r}: " + "; ".join(problems))

    def find_problems(self) -> list[str]:
        problems: list[str] = []
        if not self.allowed_routes:
            problems.append("allowed_routes must not be empty")
        if not self.payload_versions or any(v < 1 for v in self.payload_versions):
            problems.append("payload_versions must be a non-empty sequence of positive integers")
        if self.max_attempts < 1:
            problems.append("max_attempts must be positive")
        if self.operation_key_resolver is not None and not self.operation_key_resolver.strip():
            problems.append("operation_key_resolver must be a non-empty import path")
        return problems

    def allows_route(self, queue_name: str) -> bool:
        return queue_name in self.allowed_routes

    def supports_version(self, payload_version: int) -> bool:
        return payload_version in self.payload_versions


@dataclass(frozen=True)
class PolicyCatalog:
    """Resolves a policy for a task import path, with per-path overrides."""

    default: TaskPolicy
    overrides: dict[str, TaskPolicy] = field(default_factory=dict)

    def get(self, task_path: str) -> TaskPolicy:
        return self.overrides.get(task_path, self.default)


def build_default_policy(max_attempts: int, allowed_routes: tuple[str, ...]) -> TaskPolicy:
    return load_task_policy("*", {"max_attempts": max_attempts, "allowed_routes": allowed_routes})


def load_task_policy(task_path: str, raw: dict[str, Any]) -> TaskPolicy:
    profile = _PolicyInput.load(raw, label=f"policy {task_path!r}")
    return TaskPolicy(task_path=task_path, **profile.model_dump())


def build_policy_catalog(
    *,
    max_attempts: int,
    allowed_routes: tuple[str, ...],
    overrides: dict[str, dict[str, Any]] | None,
) -> PolicyCatalog:
    """Build a catalog from backend configuration."""
    default = build_default_policy(max_attempts, allowed_routes)
    parsed: dict[str, TaskPolicy] = {}
    defaults = {"max_attempts": max_attempts, "allowed_routes": allowed_routes}
    for task_path, raw in parse_policy_overrides(overrides).items():
        parsed[task_path] = load_task_policy(task_path, {**defaults, **raw})
    return PolicyCatalog(default=default, overrides=parsed)


OPERATION_KEY_MAX_LENGTH = 255


def validate_operation_key(key: str | None) -> str | None:
    """Validate a producer-derived or resolver-derived operation key."""
    if key is None:
        return None
    if not isinstance(key, str) or not key.strip():
        raise ConfigurationError("operation key must be a non-empty string")
    if len(key) > OPERATION_KEY_MAX_LENGTH:
        raise ConfigurationError("operation key exceeds 255 characters")
    return key


def derive_operation_key(
    policy: TaskPolicy, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> str | None:
    """Derive the business operation key from a task policy's declared resolver.

    The resolver is keyed to a single import path, so it receives only the
    validated arguments and returns the same identity the handler will derive.
    """
    if policy.operation_key_resolver is None:
        return None
    try:
        resolver = import_string(policy.operation_key_resolver)
    except ImportError as error:
        raise ConfigurationError(
            f"operation_key_resolver {policy.operation_key_resolver!r} could not be imported"
        ) from error
    return validate_operation_key(resolver(list(args), dict(kwargs)))


def get_exception_class_path(error: BaseException) -> str:
    error_type = type(error)
    return f"{error_type.__module__}.{error_type.__qualname__}"


def classify_failure(
    *, policy: TaskPolicy, error_path: str, attempts_remaining: int
) -> RetryDecision:
    """Classify a handled failure into retry, terminal failure, or hold."""
    if error_path in policy.permanent_exception_paths:
        return RetryDecision.FAIL
    if attempts_remaining <= 0:
        return RetryDecision.FAIL
    if error_path in policy.retryable_exception_paths:
        return RetryDecision.RETRY
    return RetryDecision.RETRY if policy.retry_safe else RetryDecision.FAIL


def classify_ambiguity(*, policy: TaskPolicy, attempts_remaining: int) -> RetryDecision:
    """Classify an uncertain external outcome after an abandoned attempt."""
    if attempts_remaining <= 0:
        return RetryDecision.FAIL
    return RetryDecision.RETRY if policy.ambiguous_retry else RetryDecision.HOLD


def compute_backoff(attempt_number: int, *, retry_after: timedelta | None = None) -> timedelta:
    """Capped exponential backoff with jitter, honoring a provider Retry-After."""
    upper = min(MAX_BACKOFF_SECONDS, 60 * (2 ** max(0, attempt_number - 1)))
    delay = random.uniform(60.0, max(60.0, float(upper)))
    if retry_after is not None:
        delay = max(delay, retry_after.total_seconds())
    return timedelta(seconds=delay)
