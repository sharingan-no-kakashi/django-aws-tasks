"""Production backend submission preserves durable acceptance identity."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connections, transaction
from django.tasks import task_backends
from django.test import override_settings
from django.utils import timezone

from django_tasks_aws import submit
from django_tasks_aws.exceptions import SubmissionConflict
from django_tasks_aws.models import TaskRecord
from tests import testing_settings
from tests.demo_tasks import add

DB = "postgres"
pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=[DB], transaction=True)]


@pytest.fixture
def submission_backend():
    with override_settings(TASKS=testing_settings.TASKS):
        yield task_backends["default"]


def test_submit_is_transactional_and_separates_metadata(submission_backend):
    with transaction.atomic(using=DB):
        result = submit(
            add, args=(2, 3), key="invoice:v1", scope="tenant-1", operation_key="invoice:1"
        )
        assert TaskRecord.objects.using(DB).get(pk=result.id).operation_key == "invoice:1"
        transaction.set_rollback(True, using=DB)
    assert not TaskRecord.objects.using(DB).exists()


def test_submission_identity_includes_business_key_and_absolute_schedule(submission_backend):
    scheduled = add.using(run_after=timezone.now() + timedelta(minutes=1))
    first = submit(scheduled, args=(1, 2), key="same", operation_key="effect-1")
    assert submit(scheduled, args=(1, 2), key="same", operation_key="effect-1").id == first.id
    with pytest.raises(SubmissionConflict):
        submit(scheduled, args=(1, 2), key="same", operation_key="effect-2")
    with pytest.raises(SubmissionConflict):
        submit(add, args=(1, 2), key="same", operation_key="effect-1")
    assert TaskRecord.objects.using(DB).count() == 1


def test_competing_submitters_return_one_task(submission_backend):
    def accept():
        connections.close_all()
        try:
            return submit(add, args=(1, 2), key="concurrent", operation_key="effect").id
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = tuple(pool.map(lambda _: accept(), range(4)))
    assert len(set(identities)) == 1
    assert TaskRecord.objects.using(DB).count() == 1
