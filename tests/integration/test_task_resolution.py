"""Workers resolve current task paths and preserve the recorded route."""

import pytest

from django_tasks_aws.models import TaskStatus
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services.worker import MessageDecision, handle_task
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_current_task_path_preserves_recorded_route(test_backend):
    test_backend.allowed_task_modules = ("tests",)
    record = make_task_record(task_name="tests.demo_tasks.add", queue_name="documents")
    assert (
        handle_task(
            str(record.id),
            backend=test_backend,
            worker=WorkerIdentity("documents", ""),
            remaining_seconds=60,
        )
        == MessageDecision.ACK
    )
    record.refresh_from_db()
    assert record.status == TaskStatus.SUCCESSFUL and record.result == 3
    assert record.task_name == "tests.demo_tasks.add" and record.queue_name == "documents"
