"""Worker configuration validation."""

import pytest

from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.schemas import WorkerConfig, load_route_manifest


def valid_profile(**overrides) -> dict:
    data = {
        "source_queue_arn": "arn:aws:sqs:us-east-1:123:short",
        "source_queue_url": "https://sqs.us-east-1.amazonaws.com/123/short",
        "timeout_seconds": 60,
        "lease_seconds": 90,
        "visibility_timeout_seconds": 360,
        "max_receive_count": 5,
        "concurrency": 10,
        "dispatch_limit": 12,
        "redispatch_seconds": 300,
    }
    data.update(overrides)
    return data


def test_valid_profile_loads() -> None:
    config = WorkerConfig.from_dict("short", valid_profile())
    assert config.name == "short"
    assert config.lease_seconds == 90


def test_missing_field_raises() -> None:
    with pytest.raises(ConfigurationError):
        WorkerConfig.from_dict("short", {"source_queue_arn": "arn"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"lease_seconds": 60},  # lease == timeout is unsafe
        {"lease_seconds": 59},  # lease < timeout
        {"visibility_timeout_seconds": 359},  # < 6x timeout
        {"timeout_seconds": 0},
        {"timeout_seconds": 5},
        {"concurrency": 0},
        {"dispatch_limit": 0},
        {"max_receive_count": 1},
        {"redispatch_seconds": -1},
        {"paused": "false"},
        {"concurrency": True},
        {"timeout_seconds": "60"},
        {"source_queue_url": 123},
        {"dispatch_limt": 12},
    ],
)
def test_unsafe_profile_raises(overrides) -> None:
    with pytest.raises(ConfigurationError):
        WorkerConfig.from_dict("short", valid_profile(**overrides))


def test_load_route_manifest_rejects_non_mapping() -> None:
    with pytest.raises(ConfigurationError):
        load_route_manifest(["short"])


def test_load_route_manifest_builds_typed_records() -> None:
    manifest = load_route_manifest({"short": valid_profile()})
    assert isinstance(manifest["short"], WorkerConfig)


def test_routes_reject_duplicate_source_identity():
    with pytest.raises(ConfigurationError, match="distinct"):
        load_route_manifest({"short": valid_profile(), "documents": valid_profile()})


@pytest.mark.parametrize("raw", [{"short": None}, {"short": []}, {1: valid_profile()}, {}])
def test_malformed_profiles_raise_configuration_error(raw):
    with pytest.raises(ConfigurationError):
        load_route_manifest(raw)


def test_queue_arn_and_url_must_agree():
    with pytest.raises(ConfigurationError, match="disagree"):
        load_route_manifest(
            {
                "short": valid_profile(
                    source_queue_url="https://sqs.us-east-1.amazonaws.com/123/other"
                )
            }
        )
