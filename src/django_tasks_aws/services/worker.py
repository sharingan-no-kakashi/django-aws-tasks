"""SQS delivery handling and the shared validate, claim, execute worker flow."""

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from aws_lambda_powertools.utilities.batch import (
    BatchProcessor,
    EventType,
    process_partial_response,
)
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord
from django.db import DatabaseError

from django_tasks_aws import telemetry
from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.databases import context
from django_tasks_aws.exceptions import ConfigurationError, InvariantViolation
from django_tasks_aws.models import HoldReason, TaskRecord, TaskStatus
from django_tasks_aws.policies import TaskPolicy
from django_tasks_aws.schemas import RouteManifest, WorkerIdentity
from django_tasks_aws.serialization import SerializationError, parse_envelope
from django_tasks_aws.services import claims, execution, invariants, outcomes
from django_tasks_aws.services.execution import ExecutionBudget
from django_tasks_aws.services.inspection import get_database_time
from django_tasks_aws.telemetry import tracer

FINALIZATION_MARGIN_SECONDS = 5.0
WORKER_NAME_ENV = "TASK_WORKER_NAME"
SOURCE_ARN_ENV = "TASK_WORKER_SOURCE_ARN"


class MessageDecision(StrEnum):
    ACK = "ack"
    DELIVERY_FAILURE = "delivery-failure"


class DeliveryFailure(Exception):
    """Tell the SQS batch processor to leave this message for redelivery."""


@dataclass(frozen=True)
class WorkerInvocation:
    backend: DatabaseSqsBackend
    worker: WorkerIdentity
    remaining_seconds: float
    context: Any

    def handle_record(self, record: SQSRecord) -> str:
        decision = process_record(
            record.raw_event,
            backend=self.backend,
            worker=self.worker,
            remaining_seconds=self.remaining_seconds,
            context=self.context,
        )
        if decision is MessageDecision.DELIVERY_FAILURE:
            raise DeliveryFailure(record.message_id)
        return record.message_id


@context.manage_invocation_connections()
def process_event(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from django_tasks_aws.configuration import get_invocation_backend

    backend = get_invocation_backend()
    worker = get_worker_identity()
    validate_worker_identity(worker, backend.routes)
    invocation = WorkerInvocation(
        backend=backend,
        worker=worker,
        remaining_seconds=claims.get_remaining_lifetime(get_remaining_millis(context)),
        context=context,
    )
    processor = BatchProcessor(event_type=EventType.SQS, raise_on_entire_batch_failure=False)
    return cast(
        dict[str, Any],
        process_partial_response(
            event=event,
            record_handler=invocation.handle_record,
            processor=processor,
            context=context,
        ),
    )


def process_record(
    record: dict[str, Any],
    *,
    backend: DatabaseSqsBackend,
    worker: WorkerIdentity,
    remaining_seconds: float,
    context: Any,
) -> MessageDecision:
    task_id = get_delivery_task_id(record, worker)
    if task_id is None:
        return MessageDecision.DELIVERY_FAILURE
    try:
        return handle_task(
            task_id,
            backend=backend,
            worker=worker,
            remaining_seconds=remaining_seconds,
            metadata=build_attempt_metadata(record, context),
            budget=build_delivery_budget(remaining_seconds, context),
        )
    except (DatabaseError, InvariantViolation) as error:
        report_delivery_failure(task_id, worker, error)
        return MessageDecision.DELIVERY_FAILURE


def get_delivery_task_id(record: dict[str, Any], worker: WorkerIdentity) -> str | None:
    if record.get("eventSourceARN") != worker.source_arn:
        return None
    try:
        envelope = parse_envelope(json.loads(record.get("body", "")))
        UUID(envelope.task_id)
    except (SerializationError, ValueError, TypeError):
        return None
    return envelope.task_id


def build_delivery_budget(seconds: float, context: Any) -> ExecutionBudget:
    return ExecutionBudget.after(
        seconds,
        remaining_time=lambda: claims.get_remaining_lifetime(get_remaining_millis(context)),
    )


def report_delivery_failure(task_id: str, worker: WorkerIdentity, error: Exception) -> None:
    event = (
        "delivery.invariant_failure"
        if isinstance(error, InvariantViolation)
        else "delivery.database_failure"
    )
    telemetry.log_event(event, task_id=task_id, queue=worker.name, error_type=type(error).__name__)


def handle_task(
    task_id: str,
    *,
    backend: DatabaseSqsBackend,
    worker: WorkerIdentity,
    remaining_seconds: float,
    metadata: claims.AttemptMetadata | None = None,
    budget: ExecutionBudget | None = None,
    lease_seconds: int | None = None,
) -> MessageDecision:
    """Inspect accepted work, check execution limits, then claim and execute it."""
    budget = budget or ExecutionBudget.after(remaining_seconds)
    trace_task(task_id, backend, worker)
    record = TaskRecord.objects.using(backend.database_alias).filter(pk=task_id).first()
    if record is None:
        return MessageDecision.DELIVERY_FAILURE
    decision = classify_task(record, backend, worker)
    if decision is not None:
        return decision
    policy = get_permitted_task_policy(record, backend, worker)
    if policy is None:
        return MessageDecision.ACK
    lease = get_available_worker_lease(backend, worker, budget, lease_seconds)
    if lease is None:
        return MessageDecision.DELIVERY_FAILURE
    return claim_and_execute(
        record,
        backend,
        worker,
        policy=policy,
        budget=budget,
        lease_seconds=lease,
        metadata=metadata,
    )


def classify_task(
    record: TaskRecord, backend: DatabaseSqsBackend, worker: WorkerIdentity
) -> MessageDecision | None:
    """Acknowledge ineligible work; reject misrouting and inconsistent history."""
    if record.queue_name != worker.name or record.backend_alias != backend.alias:
        return MessageDecision.DELIVERY_FAILURE
    if invariants.find_violations(
        backend.database_alias, backend_alias=backend.alias, task_id=str(record.pk)
    ):
        raise InvariantViolation(f"Task {record.pk} has inconsistent execution history")
    if is_task_unrunnable(record, backend.database_alias):
        return MessageDecision.ACK
    return None


def is_task_unrunnable(record: TaskRecord, database_alias: str) -> bool:
    return (
        record.status in (TaskStatus.SUCCESSFUL, TaskStatus.FAILED, TaskStatus.RUNNING)
        or record.held_at is not None
        or record.available_at > get_database_time(database_alias)
    )


def get_permitted_task_policy(
    record: TaskRecord, backend: DatabaseSqsBackend, worker: WorkerIdentity
) -> TaskPolicy | None:
    """Return the policy, or hold work with an unsupported payload or route."""
    policy = backend.policies.get(record.task_name)
    if policy.supports_version(record.payload_version) and policy.allows_route(worker.name):
        return policy
    outcomes.hold_pending(
        task_id=str(record.pk),
        hold_reason=HoldReason.INCOMPATIBLE_PAYLOAD,
        database_alias=backend.database_alias,
    )
    return None


def get_available_worker_lease(
    backend: DatabaseSqsBackend,
    worker: WorkerIdentity,
    budget: ExecutionBudget,
    lease_seconds: int | None,
) -> int | None:
    route = backend.routes.get(worker.name)
    if route is None or route.paused:
        return None
    remaining = budget.remaining
    if remaining <= FINALIZATION_MARGIN_SECONDS:
        return None
    lease = lease_seconds or route.lease_seconds
    if not claims.lease_covers(
        lease_seconds=lease,
        remaining_seconds=remaining,
        margin_seconds=FINALIZATION_MARGIN_SECONDS,
    ):
        return None
    return lease


def claim_and_execute(
    record: TaskRecord,
    backend: DatabaseSqsBackend,
    worker: WorkerIdentity,
    *,
    policy: TaskPolicy,
    budget: ExecutionBudget,
    lease_seconds: int,
    metadata: claims.AttemptMetadata | None,
) -> MessageDecision:
    claimed = claims.claim_task(
        task_id=str(record.pk),
        queue_name=worker.name,
        worker_name=worker.name,
        lease_seconds=lease_seconds,
        database_alias=backend.database_alias,
        metadata=metadata,
        backend_alias=backend.alias,
    )
    if claimed is not None:
        execution.execute_claimed_task(
            claimed=claimed,
            record=record,
            policy=policy,
            allowed_modules=backend.allowed_task_modules,
            budget=budget,
            database_alias=backend.database_alias,
        )
    return MessageDecision.ACK


def trace_task(task_id: str, backend: DatabaseSqsBackend, worker: WorkerIdentity) -> None:
    tracer.put_annotation(key="task_id", value=task_id)
    tracer.put_annotation(key="queue", value=worker.name)
    tracer.put_annotation(key="backend", value=backend.alias)


def get_worker_identity() -> WorkerIdentity:
    return WorkerIdentity(
        name=os.environ.get(WORKER_NAME_ENV, ""),
        source_arn=os.environ.get(SOURCE_ARN_ENV, ""),
    )


def validate_worker_identity(worker: WorkerIdentity, routes: RouteManifest) -> None:
    route = routes.get(worker.name)
    if route is None or route.source_queue_arn != worker.source_arn:
        raise ConfigurationError("Worker name/source ARN does not match the route manifest")


def get_remaining_millis(context: Any) -> int:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    if getter is not None:
        return int(getter())
    return 60_000


def build_attempt_metadata(record: dict[str, Any], context: Any) -> claims.AttemptMetadata:
    return claims.AttemptMetadata(
        lambda_request_id=getattr(context, "aws_request_id", "") if context else "",
        log_group=getattr(context, "log_group_name", "") if context else "",
        log_stream=getattr(context, "log_stream_name", "") if context else "",
        sqs_message_id=record.get("messageId", ""),
        **get_release_metadata(context),
    )


def get_release_metadata(context):
    from django_tasks_aws import __version__

    return {
        "aws_region": os.environ.get("AWS_REGION", ""),
        "worker_version": os.environ.get("TASK_APPLICATION_VERSION", __version__),
        "function_version": getattr(context, "function_version", "")
        or os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", ""),
    }
