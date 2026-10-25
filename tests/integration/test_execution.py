"""Task execution wrapper and effect-safety classification."""

import pytest
from django.utils import timezone

from django_tasks_aws import policies
from django_tasks_aws.models import TaskStatus
from django_tasks_aws.services import claims, execution
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def make_policy(**overrides) -> policies.TaskPolicy:
    kwargs = {
        "task_path": "*",
        "allowed_routes": ("short", "documents"),
        "payload_versions": (1,),
        "max_attempts": 5,
    }
    kwargs.update(overrides)
    return policies.TaskPolicy(**kwargs)


def run(task_name: str, policy: policies.TaskPolicy, args=("x",), max_attempts=5):
    record = make_task_record(
        task_name=task_name,
        payload={"args": list(args), "kwargs": {}},
        max_attempts=max_attempts,
    )
    claimed = claims.claim_task(
        task_id=record.id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    outcome = execution.execute_claimed_task(
        claimed=claimed,
        record=record,
        policy=policy,
        allowed_modules=("tests.demo_tasks",),
        database_alias="postgres",
    )
    record.refresh_from_db()
    return outcome, record


def test_success():
    outcome, record = run("tests.demo_tasks.add", make_policy(), args=(1, 2))
    assert outcome == "success"
    assert record.status == TaskStatus.SUCCESSFUL
    assert record.result == 3


def test_permanent_failure_is_terminal():
    outcome, record = run("tests.demo_tasks.fail_permanently", make_policy())
    assert outcome == "failure"
    assert record.status == TaskStatus.FAILED


def test_transient_failure_retries_when_retryable():
    policy = make_policy(retryable_exception_paths=("builtins.RuntimeError",))
    outcome, record = run("tests.demo_tasks.fail_transiently", policy)
    assert outcome == "retry"
    assert record.status == TaskStatus.PENDING
    assert record.available_at > timezone.now()


def test_transient_failure_is_terminal_on_last_attempt():
    policy = make_policy(max_attempts=1, retryable_exception_paths=("builtins.RuntimeError",))
    outcome, record = run("tests.demo_tasks.fail_transiently", policy, max_attempts=1)
    assert outcome == "failure"
    assert record.status == TaskStatus.FAILED


def test_ambiguous_outcome_holds_when_not_repeat_safe():
    outcome, record = run("tests.demo_tasks.ambiguous", make_policy())
    assert outcome == "hold"
    assert record.status == TaskStatus.PENDING
    assert record.held_at is not None


def test_ambiguous_outcome_retries_when_repeat_safe():
    outcome, record = run("tests.demo_tasks.ambiguous", make_policy(ambiguous_retry=True))
    assert outcome == "retry"
    assert record.status == TaskStatus.PENDING
