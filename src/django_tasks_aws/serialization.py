"""Versioned JSON serialization for task arguments, results, and envelopes.

Arguments are validated before a task is inserted, so an unsafe value is
rejected at enqueue time rather than surviving to a silent type change during
encoding. Payload hashing uses a documented canonical form so deduplication and
conflict detection are stable.
"""

import hashlib
import json
import math
from typing import Any

from aws_lambda_powertools.utilities.parser import parse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from django_tasks_aws.exceptions import DjangoTasksAWSError
from django_tasks_aws.schemas import Envelope

INLINE_ARGUMENT_LIMIT = 32 * 1024
INLINE_RESULT_LIMIT = 64 * 1024
ENVELOPE_SCHEMA_VERSION = 1
CURRENT_PAYLOAD_VERSION = 1


class _EnvelopeInput(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: int = Field(ge=ENVELOPE_SCHEMA_VERSION, le=ENVELOPE_SCHEMA_VERSION)
    task_id: str = Field(min_length=1)


class SerializationError(DjangoTasksAWSError):
    """Arguments or results are not safely JSON-serializable."""


def build_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Shape the argument document stored on a task."""
    return {"args": list(args), "kwargs": dict(kwargs)}


def validate_payload(value: Any) -> None:
    """Reject values that would silently change during JSON encoding."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SerializationError("non-finite floats are not supported")
        return
    if isinstance(value, list):
        for item in value:
            validate_payload(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SerializationError("object keys must be strings")
            validate_payload(item)
        return
    raise SerializationError(f"unsupported value type: {type(value).__name__}")


def dumps(value: Any) -> str:
    """Canonical JSON: stable key order, compact separators."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_payload_hash(task_path: str, payload_version: int, payload: dict[str, Any]) -> str:
    """Hash the canonical task identity, version, and argument representation."""
    canonical = dumps([task_path, payload_version, payload])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_argument_size(payload: dict[str, Any]) -> None:
    if len(dumps(payload).encode("utf-8")) > INLINE_ARGUMENT_LIMIT:
        raise SerializationError(
            f"task arguments exceed the {INLINE_ARGUMENT_LIMIT}-byte inline limit"
        )


def serialize_result(value: Any) -> Any:
    """Validate and normalize a handler result for durable storage."""
    validate_payload(value)
    encoded = dumps(value)
    if len(encoded.encode("utf-8")) > INLINE_RESULT_LIMIT:
        raise SerializationError(f"task result exceeds the {INLINE_RESULT_LIMIT}-byte inline limit")
    return value


def serialize_envelope(task_id: str) -> dict[str, Any]:
    return {"schema_version": ENVELOPE_SCHEMA_VERSION, "task_id": task_id}


def parse_envelope(body: Any) -> Envelope:
    """Parse a queue envelope, rejecting malformed or unknown versions."""
    try:
        envelope = parse(event=body, model=_EnvelopeInput)
    except ValidationError as error:
        raise SerializationError("invalid task envelope") from error
    return Envelope(schema_version=envelope.schema_version, task_id=envelope.task_id)
