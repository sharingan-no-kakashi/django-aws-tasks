"""Policy decisions: retry classification, ambiguity, and backoff."""

from datetime import timedelta

import pytest

from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.policies import (
    RetryDecision,
    TaskPolicy,
    build_policy_catalog,
    classify_ambiguity,
    classify_failure,
    compute_backoff,
    derive_operation_key,
    validate_operation_key,
)


def make_policy(**overrides) -> TaskPolicy:
    kwargs = {
        "task_path": "t.path",
        "allowed_routes": ("short", "documents"),
        "payload_versions": (1,),
        "max_attempts": 5,
    }
    kwargs.update(overrides)
    return TaskPolicy(**kwargs)


def test_policy_rejects_empty_routes() -> None:
    with pytest.raises(ConfigurationError):
        make_policy(allowed_routes=())


def test_policy_rejects_invalid_max_attempts() -> None:
    with pytest.raises(ConfigurationError):
        make_policy(max_attempts=0)


@pytest.mark.parametrize(
    ("error_path", "remaining", "retry_safe", "expected"),
    [
        ("x.Permanent", 3, False, RetryDecision.FAIL),
        ("x.Transient", 3, False, RetryDecision.RETRY),
        ("x.Unknown", 3, False, RetryDecision.FAIL),
        ("x.Unknown", 3, True, RetryDecision.RETRY),
        ("x.Transient", 0, False, RetryDecision.FAIL),
    ],
)
def test_classify_failure_decision_table(error_path, remaining, retry_safe, expected) -> None:
    policy = make_policy(
        retryable_exception_paths=("x.Transient",),
        permanent_exception_paths=("x.Permanent",),
        retry_safe=retry_safe,
    )
    assert (
        classify_failure(policy=policy, error_path=error_path, attempts_remaining=remaining)
        is expected
    )


@pytest.mark.parametrize(
    ("remaining", "ambiguous_retry", "expected"),
    [
        (3, True, RetryDecision.RETRY),
        (3, False, RetryDecision.HOLD),
        (0, True, RetryDecision.FAIL),
    ],
)
def test_classify_ambiguity_decision_table(remaining, ambiguous_retry, expected) -> None:
    policy = make_policy(ambiguous_retry=ambiguous_retry)
    assert classify_ambiguity(policy=policy, attempts_remaining=remaining) is expected


def test_backoff_is_within_bounds_when_seeded() -> None:
    import random

    random.seed(0)
    delay = compute_backoff(1)
    assert delay.total_seconds() == pytest.approx(60.0)

    random.seed(0)
    delay = compute_backoff(5)
    assert 60.0 <= delay.total_seconds() <= 60 * 60


def test_backoff_honors_retry_after_lower_bound() -> None:
    import random

    random.seed(0)
    delay = compute_backoff(1, retry_after=timedelta(seconds=120))
    assert delay.total_seconds() >= 120


def test_build_policy_catalog_applies_overrides() -> None:
    catalog = build_policy_catalog(
        max_attempts=5,
        allowed_routes=("short", "documents"),
        overrides={"tests.demo_tasks.render": {"allowed_routes": ["documents"], "max_attempts": 3}},
    )
    default = catalog.get("tests.demo_tasks.add")
    restricted = catalog.get("tests.demo_tasks.render")

    assert default.allowed_routes == ("short", "documents")
    assert default.max_attempts == 5
    assert restricted.allowed_routes == ("documents",)
    assert restricted.max_attempts == 3
    assert restricted.allows_route("documents")
    assert not restricted.allows_route("short")


def test_build_policy_catalog_parses_operation_key_resolver() -> None:
    catalog = build_policy_catalog(
        max_attempts=5,
        allowed_routes=("short", "documents"),
        overrides={
            "tests.demo_tasks.add": {"operation_key_resolver": "tests.demo_tasks.add_operation_key"}
        },
    )
    assert catalog.get("tests.demo_tasks.add").operation_key_resolver == (
        "tests.demo_tasks.add_operation_key"
    )
    assert catalog.get("tests.demo_tasks.render").operation_key_resolver is None


@pytest.mark.parametrize(
    "override",
    [
        {"retry_safe": "false"},
        {"ambiguous_retry": 1},
        {"max_attempts": "5"},
        {"max_attempts": True},
        {"payload_versions": [True]},
        {"allowed_routes": "short"},
        {"retryable_exception_paths": [42]},
        {"retry_sfae": True},
        {"operation_key_resolver": 42},
    ],
)
def test_policy_configuration_rejects_coercion_and_unknown_fields(override):
    with pytest.raises(ConfigurationError):
        build_policy_catalog(
            max_attempts=5, allowed_routes=("short",), overrides={"task": override}
        )


@pytest.mark.parametrize("overrides", [[], {"task": None}, {"task": []}, {1: {}}])
def test_policy_configuration_requires_profile_mappings(overrides):
    with pytest.raises(ConfigurationError):
        build_policy_catalog(max_attempts=5, allowed_routes=("short",), overrides=overrides)


@pytest.mark.parametrize("max_attempts", ["5", True, 0])
def test_default_policy_configuration_requires_positive_integer(max_attempts):
    with pytest.raises(ConfigurationError):
        build_policy_catalog(max_attempts=max_attempts, allowed_routes=("short",), overrides=None)


def test_derive_operation_key_resolves_and_validates() -> None:
    policy = make_policy(operation_key_resolver="tests.demo_tasks.add_operation_key")
    assert derive_operation_key(policy, (1, 2), {}) == "add:1+2"


def test_derive_operation_key_returns_none_without_resolver() -> None:
    assert derive_operation_key(make_policy(), (1, 2), {}) is None


def test_derive_operation_key_rejects_unimportable_resolver() -> None:
    policy = make_policy(operation_key_resolver="missing.module.resolver")
    with pytest.raises(ConfigurationError):
        derive_operation_key(policy, (1, 2), {})


def test_validate_operation_key_contract() -> None:
    assert validate_operation_key(None) is None
    assert validate_operation_key("invoice:42") == "invoice:42"
    for invalid in (42, "", "   ", "x" * 256):
        with pytest.raises(ConfigurationError):
            validate_operation_key(invalid)
