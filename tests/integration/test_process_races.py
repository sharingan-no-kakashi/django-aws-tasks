"""Cross-process ownership races use barriers rather than timing sleeps."""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta

import pytest
from django.db import connections
from django.utils import timezone

from django_tasks_aws.models import AttemptOutcome, AttemptStatus, TaskAttempt, TaskStatus
from tests.support.factories import make_task_record
from tests.support.process_races import initialize, race_claim, race_finalize_or_reap

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def run_race(function, arguments):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    database = dict(connections["postgres"].settings_dict)
    with ProcessPoolExecutor(
        max_workers=2, mp_context=context, initializer=initialize, initargs=(database, barrier)
    ) as pool:
        futures = [pool.submit(function, argument) for argument in arguments]
        return [future.result(timeout=30) for future in futures]


def test_spawned_workers_cannot_both_claim_current_work():
    record = make_task_record()
    results = run_race(race_claim, [str(record.id), str(record.id)])
    assert sum(result is not None for result in results) == 1
    record.refresh_from_db()
    assert record.attempts_started == 1 and record.status == TaskStatus.RUNNING
    assert TaskAttempt.objects.using("postgres").filter(task=record).count() == 1


def test_spawned_finalizer_cannot_overwrite_reaper_after_expiry():
    from django_tasks_aws.services.claims import claim_task

    record = make_task_record()
    claimed = claim_task(
        task_id=str(record.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    type(record).objects.using("postgres").filter(pk=record.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    results = run_race(
        race_finalize_or_reap,
        [
            ("finalize", str(record.id), claimed.attempt_id),
            ("reap", str(record.id), claimed.attempt_id),
        ],
    )
    assert results[0] is False and results[1] in {0, 1}
    if results[1] == 0:
        from django_tasks_aws import policies
        from django_tasks_aws.services import reconcile

        catalog = policies.build_policy_catalog(
            max_attempts=5, allowed_routes=("short",), overrides=None
        )
        assert (
            reconcile.recover_expired_attempts(
                database_alias="postgres", policy_catalog=catalog, limit=10
            )
            == 1
        )
    record.refresh_from_db()
    attempt = record.attempts.get()
    assert record.status == TaskStatus.PENDING and record.held_at is not None
    assert attempt.status == AttemptStatus.ABANDONED and attempt.outcome == AttemptOutcome.UNKNOWN


def test_spawned_publishers_reserve_only_one_delivery():
    from tests.support.process_races import race_publish_or_claim

    record = make_task_record()
    results = run_race(race_publish_or_claim, [("publish", str(record.id))] * 2)
    assert sum(count for _, count in results) == 1
    record.refresh_from_db()
    assert record.dispatches_without_claim == 1 and record.attempts_started == 0


def test_spawned_publisher_cannot_overwrite_claim():
    from tests.support.process_races import race_publish_or_claim

    record = make_task_record()
    run_race(race_publish_or_claim, [("publish", str(record.id)), ("claim", str(record.id))])
    record.refresh_from_db()
    assert record.status == TaskStatus.RUNNING and record.attempts_started == 1
    assert record.dispatches_without_claim == 0 and record.dispatch_token is None


def test_spawned_operator_requests_create_one_replay_and_audit():
    from django_tasks_aws.models import TaskControlEvent, TaskRecord
    from tests.support.process_races import race_replay

    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    results = run_race(race_replay, [str(record.id)] * 2)
    assert results[0] == results[1]
    assert TaskRecord.objects.using("postgres").filter(replay_of_id=record.id).count() == 1
    assert (
        TaskControlEvent.objects.using("postgres").filter(request_id="process-replay").count() == 1
    )


def test_separate_workers_resolve_and_execute_persisted_import_path():
    from tests.support.process_races import race_execute

    record = make_task_record()
    assert run_race(race_execute, [str(record.id)] * 2) == ["ack", "ack"]
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL and record.result == 3
    assert record.attempts_started == 1
