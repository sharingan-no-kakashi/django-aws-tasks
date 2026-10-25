"""Task execution wrapper and effect-safety contract.

The wrapper imports the claimed Task from its stored path, invokes it with the
documented context and arguments, classifies the outcome through the task
policy, and finalizes through the ownership-checked transitions. Business
execution happens outside any coordination transaction. Remaining-attempt
decisions use the persisted ``max_attempts`` so a later policy change cannot
schedule a retry past the stored budget.
"""

import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypedDict, assert_never
from uuid import UUID

from django.tasks import TaskContext

from django_tasks_aws import policies, telemetry
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import (
    AmbiguousOutcome,
    ConfigurationError,
    ExecutionBudgetExceeded,
    PermanentFailure,
    TaskResolutionError,
)
from django_tasks_aws.import_paths import reconstruct_record_task, resolve_task
from django_tasks_aws.models import HoldReason, TaskRecord
from django_tasks_aws.policies import RetryDecision
from django_tasks_aws.progress import manage_progress_scope
from django_tasks_aws.results import get_task_result
from django_tasks_aws.serialization import SerializationError, serialize_result
from django_tasks_aws.services import outcomes
from django_tasks_aws.services.claims import ClaimedTask
from django_tasks_aws.services.inspection import get_database_time
from django_tasks_aws.telemetry import tracer
from django_tasks_aws.workflows.adapter import get_workflow_fields

FINALIZATION_SECONDS = 5.0
_current = ContextVar("django_tasks_aws_execution_budget", default=None)


@dataclass(frozen=True)
class ExecutionBudget:
    deadline: float
    remaining_time: Callable[[], float] | None = None

    @classmethod
    def after(cls, seconds, *, remaining_time=None):
        return cls(time.monotonic() + seconds, remaining_time)

    @property
    def remaining(self):
        seconds = max(0.0, self.deadline - time.monotonic())
        return min(seconds, self.remaining_time()) if self.remaining_time else seconds


def get_remaining_seconds():
    budget = _current.get()
    if budget is None:
        raise ConfigurationError("No task execution budget is active")
    return max(0.0, budget.remaining - FINALIZATION_SECONDS)


def require_time(seconds):
    if seconds < 0 or get_remaining_seconds() <= seconds:
        raise ExecutionBudgetExceeded("Insufficient execution time for the next operation")


@contextmanager
def manage_business_budget(budget):
    token = _current.set(budget)
    try:
        require_time(0)
        yield
    finally:
        _current.reset(token)


MAX_ERROR_MESSAGE_LENGTH = 2000


class ExecutionOutcome(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    HOLD = "hold"
    FAILURE = "failure"
    STALE = "stale"


class FinalizationArguments(TypedDict):
    task_id: str
    attempt_id: UUID
    error: dict
    database_alias: str
    deadline: float | None


def get_finalized_outcome(completed: bool, outcome: ExecutionOutcome) -> ExecutionOutcome:
    return outcome if completed else ExecutionOutcome.STALE


def execute_claimed_task(
    *,
    claimed: ClaimedTask,
    record: TaskRecord,
    policy: policies.TaskPolicy,
    allowed_modules: tuple[str, ...],
    database_alias: str,
    budget: ExecutionBudget | None = None,
) -> ExecutionOutcome:
    """Execute compatible work and report only an ownership-confirmed outcome."""
    record.refresh_from_db(using=database_alias)
    trace_attempt(claimed)
    if is_claim_stale(record, claimed, database_alias):
        telemetry.log_event("attempt.stale", task_id=record.id, attempt_id=claimed.attempt_id)
        return ExecutionOutcome.STALE
    try:
        task = resolve_task(claimed.task_name, allowed_modules=allowed_modules)
        bound = reconstruct_record_task(record, task=task)
    except TaskResolutionError:
        return hold_incompatible_task(claimed, database_alias)
    budget = budget or ExecutionBudget.after(
        max(0, (record.lease_expires_at - get_database_time(database_alias)).total_seconds())
    )
    return execute_body(bound, record, claimed, policy, database_alias, budget)


def trace_attempt(claimed: ClaimedTask) -> None:
    tracer.put_metadata(
        key="attempt",
        value={
            "task_id": claimed.task_id,
            "attempt_id": str(claimed.attempt_id),
            "attempt_number": claimed.attempt_number,
            "task_name": claimed.task_name,
            "worker": claimed.worker_name,
        },
        namespace="django-tasks-aws",
    )


def is_claim_stale(record: TaskRecord, claimed: ClaimedTask, database_alias: str) -> bool:
    return (
        record.lease_token != claimed.attempt_id
        or record.status != "RUNNING"
        or record.lease_expires_at is None
        or record.lease_expires_at <= get_database_time(database_alias)
    )


def hold_incompatible_task(claimed: ClaimedTask, database_alias: str) -> ExecutionOutcome:
    completed = outcomes.finalize_hold(
        task_id=claimed.task_id,
        attempt_id=claimed.attempt_id,
        hold_reason=HoldReason.INCOMPATIBLE_PAYLOAD,
        database_alias=database_alias,
    )
    return get_finalized_outcome(completed, ExecutionOutcome.HOLD)


def bind_task_context(record, claimed, database_alias):
    return telemetry.bind_context(
        task_name=record.task_name,
        backend=record.backend_alias,
        attempt_number=claimed.attempt_number,
        producer_version=record.producer_version,
        **get_workflow_fields(record.pk, database_alias),
    )


@scoped(bind_task_context, "record", "claimed", "database_alias")
def execute_body(bound, record, claimed, policy, database_alias, budget) -> ExecutionOutcome:
    context: TaskContext | None = (
        TaskContext(task_result=get_task_result(bound, record)) if bound.takes_context else None
    )
    try:
        serialized = execute_task_body(bound, record, claimed, context, database_alias, budget)
    except PermanentFailure as error:
        return finalize_decision(
            RetryDecision.FAIL, claimed, error, database_alias, budget.deadline, uncertain=False
        )
    except (AmbiguousOutcome, SerializationError, ExecutionBudgetExceeded) as error:
        return finalize_ambiguous(claimed, record, policy, error, database_alias, budget.deadline)
    except Exception as error:
        return finalize_classified(claimed, record, policy, error, database_alias, budget.deadline)
    completed = outcomes.finalize_success(
        task_id=claimed.task_id,
        attempt_id=claimed.attempt_id,
        result=serialized,
        database_alias=database_alias,
        deadline=budget.deadline,
    )
    return get_finalized_outcome(completed, ExecutionOutcome.SUCCESS)


@scoped(manage_business_budget, "budget")
@scoped(manage_progress_scope, "record.pk", "claimed.attempt_id", "database_alias")
def execute_task_body(bound, record, claimed, context, database_alias, budget):
    return serialize_result(call(bound, record, context))


def call(bound, record: TaskRecord, context: TaskContext | None) -> Any:
    args = record.payload.get("args", [])
    kwargs = record.payload.get("kwargs", {})
    if bound.takes_context:
        return bound.call(context, *args, **kwargs)
    return bound.call(*args, **kwargs)


def finalize_classified(
    claimed: ClaimedTask,
    record: TaskRecord,
    policy: policies.TaskPolicy,
    error: BaseException,
    database_alias: str,
    deadline: float | None = None,
) -> ExecutionOutcome:
    decision = policies.classify_failure(
        policy=policy,
        error_path=policies.get_exception_class_path(error),
        attempts_remaining=record.max_attempts - claimed.attempt_number,
    )
    if decision is RetryDecision.HOLD:
        return finalize_ambiguous(claimed, record, policy, error, database_alias, deadline)
    return finalize_decision(
        decision,
        claimed,
        error,
        database_alias,
        deadline,
        uncertain=isinstance(error, AmbiguousOutcome),
    )


def finalize_ambiguous(
    claimed: ClaimedTask,
    record: TaskRecord,
    policy: policies.TaskPolicy,
    error: BaseException,
    database_alias: str,
    deadline: float | None = None,
) -> ExecutionOutcome:
    decision = policies.classify_ambiguity(
        policy=policy,
        attempts_remaining=record.max_attempts - claimed.attempt_number,
    )
    return finalize_decision(
        decision,
        claimed,
        error,
        database_alias,
        deadline,
        uncertain=True,
    )


def finalize_decision(
    decision: RetryDecision,
    claimed: ClaimedTask,
    error: BaseException,
    database_alias: str,
    deadline: float | None,
    *,
    uncertain: bool,
) -> ExecutionOutcome:
    arguments: FinalizationArguments = {
        "task_id": claimed.task_id,
        "attempt_id": claimed.attempt_id,
        "error": build_error_summary(error, uncertain=uncertain),
        "database_alias": database_alias,
        "deadline": deadline,
    }
    match decision:
        case RetryDecision.RETRY:
            return finalize_retry(claimed, database_alias, arguments)
        case RetryDecision.FAIL:
            completed = outcomes.finalize_failure(**arguments)
            return get_finalized_outcome(completed, ExecutionOutcome.FAILURE)
        case RetryDecision.HOLD:
            completed = outcomes.finalize_hold(
                **arguments,
                hold_reason=HoldReason.AMBIGUOUS_OUTCOME,
            )
            return get_finalized_outcome(completed, ExecutionOutcome.HOLD)
        case _:
            assert_never(decision)


def finalize_retry(
    claimed: ClaimedTask, database_alias: str, arguments: FinalizationArguments
) -> ExecutionOutcome:
    retry_at = get_database_time(database_alias) + policies.compute_backoff(claimed.attempt_number)
    completed = outcomes.finalize_retry(**arguments, retry_at=retry_at)
    return get_finalized_outcome(completed, ExecutionOutcome.RETRY)


def build_error_summary(error: BaseException, *, uncertain: bool) -> dict:
    return {
        "error_type": policies.get_exception_class_path(error),
        "message": telemetry.redact_text(str(error), MAX_ERROR_MESSAGE_LENGTH),
        "uncertain": uncertain,
    }
