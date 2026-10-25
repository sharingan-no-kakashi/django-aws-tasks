"""Transactional enqueue: rollback, savepoints, deduplication, and dispatch.

Real-PostgreSQL evidence: these tests commit and roll back for real, and rely
on the unique submission-key constraint to prove deduplication.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import transaction

from django_tasks_aws.exceptions import SubmissionConflict
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.services import enqueue as enqueue_service
from tests.demo_tasks import add

DB = "postgres"

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


@pytest.fixture
def backend(test_backend):
    return test_backend


def enqueue_task_record(backend, **overrides):
    kwargs = {
        "task": add,
        "args": (1, 2),
        "kwargs": {},
    }
    kwargs.update(overrides)
    return enqueue_service.enqueue(backend, **kwargs)


def test_commit_creates_task(backend):
    outcome = enqueue_task_record(backend)
    assert outcome.created is True
    assert TaskRecord.objects.using(DB).count() == 1


def test_outer_rollback_creates_no_task(backend):
    with transaction.atomic(using=DB):
        enqueue_task_record(backend)
        transaction.set_rollback(True, using=DB)
    assert TaskRecord.objects.using(DB).count() == 0


def test_nested_savepoint_rollback_leaves_outer_intact(backend):
    with transaction.atomic(using=DB):
        with transaction.atomic(using=DB):
            enqueue_task_record(backend)
            transaction.set_rollback(True, using=DB)
        # outer work survives the inner rollback
        enqueue_task_record(backend)
    assert TaskRecord.objects.using(DB).count() == 1


def test_identical_submission_returns_original(backend):
    first = enqueue_task_record(backend, submission_key="k1")
    second = enqueue_task_record(backend, submission_key="k1")

    assert first.record.id == second.record.id
    assert second.created is False
    assert TaskRecord.objects.using(DB).count() == 1


def test_changed_work_conflicts(backend):
    enqueue_task_record(backend, submission_key="k1", args=(1, 2))
    with pytest.raises(SubmissionConflict):
        enqueue_task_record(backend, submission_key="k1", args=(3, 4))


def test_missing_submission_hash_is_not_accepted_as_identical_work(backend):
    outcome = enqueue_task_record(backend, submission_key="missing-hash")
    TaskRecord.objects.using(DB).filter(pk=outcome.record.pk).update(submission_hash="")

    with pytest.raises(SubmissionConflict):
        enqueue_task_record(backend, submission_key="missing-hash")


def test_concurrent_identical_submissions_share_one_record(backend):
    def submit(_):
        try:
            return enqueue_task_record(backend, submission_key="shared").record.id
        finally:
            from django.db import connections

            connections[DB].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(submit, range(2)))

    assert ids[0] == ids[1]
    assert TaskRecord.objects.using(DB).count() == 1


def test_commit_fires_dispatch_callback(backend, monkeypatch):
    from django_tasks_aws.services import dispatch

    calls: list[str] = []
    monkeypatch.setattr(dispatch, "publish_task", lambda task_id, **kwargs: calls.append(task_id))

    with transaction.atomic(using=DB):
        outcome = enqueue_task_record(backend)

    assert calls == [str(outcome.record.id)]
