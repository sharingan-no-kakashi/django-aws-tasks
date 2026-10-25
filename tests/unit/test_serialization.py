"""Serialization: JSON safety, canonical hashing, and envelopes."""

import math

import pytest

from django_tasks_aws import serialization
from django_tasks_aws.exceptions import DjangoTasksAWSError
from django_tasks_aws.serialization import (
    build_payload,
    compute_payload_hash,
    parse_envelope,
    validate_payload,
)


def test_validate_payload_accepts_json_values() -> None:
    validate_payload({"a": [1, "x", True, None], "b": 2.5})


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, {1: "int-key"}, object()],
)
def test_validate_payload_rejects_unsafe_values(value) -> None:
    with pytest.raises(DjangoTasksAWSError):
        validate_payload(value)


def test_payload_hash_is_stable_and_order_independent() -> None:
    payload = build_payload((1, 2), {"b": 1, "a": 2})
    same = build_payload((1, 2), {"a": 2, "b": 1})
    assert compute_payload_hash("t.path", 1, payload) == compute_payload_hash("t.path", 1, same)


def test_payload_hash_differs_on_change() -> None:
    base = build_payload((1,), {})
    changed = build_payload((2,), {})
    assert compute_payload_hash("t.path", 1, base) != compute_payload_hash("t.path", 1, changed)
    assert compute_payload_hash("t.path", 1, base) != compute_payload_hash("t.path", 2, base)


def test_parse_envelope_round_trip() -> None:
    envelope = parse_envelope(serialization.serialize_envelope("task-1"))
    assert envelope.task_id == "task-1"
    assert envelope.schema_version == 1


@pytest.mark.parametrize(
    "body",
    [{"schema_version": 2, "task_id": "x"}, {"task_id": "x"}, {"schema_version": 1}, "not-dict"],
)
def test_parse_envelope_rejects_malformed(body) -> None:
    with pytest.raises(DjangoTasksAWSError):
        parse_envelope(body)
