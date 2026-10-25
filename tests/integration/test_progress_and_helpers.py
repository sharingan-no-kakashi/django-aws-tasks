"""Progress remains advisory, rate-limited, and owned by its actual attempt."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections, transaction
from django.tasks import task_backends
from django.test import override_settings
from django.utils import timezone

from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.models import TaskAttempt, TaskRecord
from django_tasks_aws.progress import manage_progress_scope, report_progress
from django_tasks_aws.services import claims, outcomes
from django_tasks_aws.testing import TaskHarness
from tests import testing_settings
from tests.demo_tasks import add, report_measurement

DB = "postgres"
pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=[DB], transaction=True)]


@pytest.fixture
def harness():
    with override_settings(TASKS=testing_settings.TASKS):
        yield TaskHarness(task_backends["default"])


def claim(task_id):
    result = claims.claim_task(
        task_id=task_id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias=DB,
    )
    assert result is not None
    return result


def test_task_reports_progress_without_renewing_lease(harness):
    result = report_measurement.enqueue()
    harness.run_pending()
    harness.assert_status(result.id, "SUCCESSFUL", attempts=1)
    assert harness.backend.get_result(result.id).return_value == [True, False]
    attempt = TaskAttempt.objects.using(DB).get(task_id=result.id)
    assert attempt.progress["completed"] == 1
    assert attempt.progress_at < attempt.lease_expires_at
    public = harness.detail(result.id)["attempts"][0]
    assert public["progress_state"] == "previous"
    assert "message" not in public["progress"]
    assert (
        harness.detail(result.id, sensitive=True)["attempts"][0]["progress"]["message"]
        == "private measurement"
    )


def test_old_attempt_cannot_overwrite_retry_progress(harness):
    result = add.enqueue(1, 2)
    first = claim(result.id)
    assert outcomes.finalize_retry(
        task_id=result.id, attempt_id=first.attempt_id, retry_at=timezone.now(), database_alias=DB
    )
    second = claim(result.id)
    with manage_progress_scope(result.id, second.attempt_id, DB):
        assert report_progress(stage="new attempt", completed=1, total=1)
    with manage_progress_scope(result.id, first.attempt_id, DB):
        assert not report_progress(stage="obsolete", completed=1, total=1)
    assert (
        TaskAttempt.objects.using(DB).get(pk=second.attempt_id).progress["stage"] == "new attempt"
    )
    assert TaskAttempt.objects.using(DB).get(pk=first.attempt_id).progress == {}


def test_progress_and_finalization_serialize(harness):
    result = add.enqueue(1, 2)
    claimed = claim(result.id)
    barrier = Barrier(2)

    def report():
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            with manage_progress_scope(result.id, claimed.attempt_id, DB):
                return report_progress(stage="racing")
        finally:
            connections.close_all()

    def finalize():
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            return outcomes.finalize_success(
                task_id=result.id, attempt_id=claimed.attempt_id, result=3, database_alias=DB
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        report_future, final_future = pool.submit(report), pool.submit(finalize)
        assert report_future.result(timeout=10) in (True, False)
        assert final_future.result(timeout=10)
    with manage_progress_scope(result.id, claimed.attempt_id, DB):
        assert not report_progress(stage="after completion")
    harness.assert_status(result.id, "SUCCESSFUL", attempts=1)
    assert TaskRecord.objects.using(DB).get(pk=result.id).lease_token is None


def test_harness_refuses_uncommitted_execution(harness):
    with transaction.atomic(using=DB), pytest.raises(ConfigurationError, match="Commit"):
        harness.run_pending()
