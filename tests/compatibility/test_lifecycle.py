"""Real-engine contracts shared by every compatibility matrix cell.

SQL, transactions, migrations, and locks are real; SQS publication uses the
in-memory transport. PostgreSQL-specific query-plan and raw-driver checks stay
in tests/integration.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connections, transaction
from django.tasks import TaskResultStatus, task_backends
from django.utils import timezone

from django_tasks_aws import submit
from django_tasks_aws.databases import get_database_adapter
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.exceptions import SubmissionConflict
from django_tasks_aws.models import TaskAttempt, TaskRecord, TaskStatus
from django_tasks_aws.policies import TaskPolicy
from django_tasks_aws.services import claims, dispatch, execution, outcomes
from django_tasks_aws.services.dispatch import DispatchResult
from tests.demo_tasks import add
from tests.support.factories import make_task_record

pytestmark = pytest.mark.django_db(transaction=True)
DB = "default"


@pytest.fixture
def backend():
    return task_backends["default"]


def claim(task_id):
    return claims.claim_task(
        task_id=task_id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias=DB,
    )


def execute(record):
    claimed = claim(record.id)
    assert claimed is not None
    return execution.execute_claimed_task(
        claimed=claimed,
        record=record,
        policy=TaskPolicy(
            task_path="*",
            allowed_routes=("short", "documents"),
            payload_versions=(1,),
            max_attempts=5,
        ),
        allowed_modules=("tests.demo_tasks",),
        database_alias=DB,
    )


def test_writer_checks_accept_engine():
    get_database_adapter(DB).require_writer(connections[DB])


def test_enqueue_publish_execute_and_read_result(backend, fake_transport, manifest):
    accepted = backend.enqueue(add, (1, 2), {})
    record = TaskRecord.objects.get(pk=accepted.id)
    published = dispatch.publish_task(
        record.id, transport=fake_transport, routes=manifest, database_alias=DB
    )
    assert published is DispatchResult.DISPATCHED
    assert len(fake_transport.sent) == 1
    assert execute(record) == "success"
    result = backend.get_result(accepted.id)
    assert result.status is TaskResultStatus.SUCCESSFUL
    assert result.return_value == 3
    assert TaskAttempt.objects.filter(task_id=record.id).count() == 1


def test_unkeyed_tasks_can_coexist(backend):
    identities = {backend.enqueue(add, (1, 2), {}).id for _ in range(3)}
    assert len(identities) == TaskRecord.objects.count() == 3


def test_keyed_submission_deduplicates_and_rejects_conflicts(backend):
    first = submit(add, args=(1, 2), key="same", scope="tenant")
    assert submit(add, args=(1, 2), key="same", scope="tenant").id == first.id
    with pytest.raises(SubmissionConflict):
        submit(add, args=(2, 3), key="same", scope="tenant")
    assert TaskRecord.objects.count() == 1


def test_rollback_removes_task_and_commit_callback(backend):
    committed = []
    with transaction.atomic(using=DB):
        backend.enqueue(add, (1, 2), {})
        transaction.on_commit(lambda: committed.append(True), using=DB)
        transaction.set_rollback(True, using=DB)
    assert TaskRecord.objects.count() == 0
    assert committed == []


def test_duplicate_claim_has_one_attempt(backend):
    accepted = backend.enqueue(add, (1, 2), {})
    assert claim(accepted.id) is not None
    assert claim(accepted.id) is None
    assert TaskAttempt.objects.filter(task_id=accepted.id).count() == 1


def test_expired_and_foreign_leases_cannot_finalize(backend):
    accepted = backend.enqueue(add, (1, 2), {})
    claimed = claim(accepted.id)
    assert claimed is not None
    assert not outcomes.finalize_success(
        task_id=accepted.id, attempt_id=uuid4(), result=99, database_alias=DB
    )
    TaskRecord.objects.filter(pk=accepted.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert not outcomes.finalize_success(
        task_id=accepted.id, attempt_id=claimed.attempt_id, result=99, database_alias=DB
    )
    assert TaskRecord.objects.get(pk=accepted.id).status == TaskStatus.RUNNING


@pytest.mark.parametrize(
    ("task_name", "expected", "status"),
    [
        ("fail_permanently", "failure", TaskStatus.FAILED),
        ("ambiguous", "hold", TaskStatus.PENDING),
    ],
)
def test_failure_classification(task_name, expected, status):
    record = make_task_record(
        using=DB, task_name=f"tests.demo_tasks.{task_name}", payload={"args": ["x"], "kwargs": {}}
    )
    assert execute(record) == expected
    record.refresh_from_db()
    assert record.status == status
    if expected == "hold":
        assert record.held_at is not None


def test_competing_workers_allocate_one_lease(backend):
    accepted = backend.enqueue(add, (1, 2), {})
    ready = Barrier(2)

    def compete():
        try:
            connections[DB].ensure_connection()
            ready.wait(timeout=10)
            return claim(accepted.id)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [
            future.result(timeout=30) for future in [workers.submit(compete) for _ in range(2)]
        ]
    assert sum(result is not None for result in results) == 1
    assert TaskAttempt.objects.filter(task_id=accepted.id).count() == 1


def read_connection_limits():
    connection = connections[DB]
    match connection.vendor:
        case "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
                )
                return cursor.fetchone()
        case "mysql":
            setting = "max_statement_time" if connection.mysql_is_mariadb else "max_execution_time"
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT @@session.{setting}, @@session.innodb_lock_wait_timeout")
                return cursor.fetchone()
        case "oracle":
            return connection.connection.call_timeout
        case _:
            raise AssertionError(f"Unexpected database vendor: {connection.vendor}")


@pytest.mark.parametrize("rollback", [False, True])
def test_coordination_restores_connection_limits(rollback):
    connections[DB].ensure_connection()
    before = read_connection_limits()
    with transaction.atomic(using=DB), manage_coordination_transaction(DB):
        assert TaskRecord.objects.count() == 0
        transaction.set_rollback(rollback, using=DB)
    assert read_connection_limits() == before


def test_expired_attempt_is_recovered_and_stale_owner_is_fenced(backend):
    from django_tasks_aws.policies import PolicyCatalog
    from django_tasks_aws.services import reconcile

    accepted = backend.enqueue(add, (1, 2), {})
    claimed = claim(accepted.id)
    assert claimed is not None
    TaskRecord.objects.filter(pk=accepted.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    policy = TaskPolicy(
        task_path="*", allowed_routes=("short",), payload_versions=(1,), max_attempts=5
    )
    assert (
        reconcile.recover_expired_attempts(
            database_alias=DB, policy_catalog=PolicyCatalog(default=policy), limit=10
        )
        == 1
    )
    record = TaskRecord.objects.get(pk=accepted.id)
    assert record.status == TaskStatus.PENDING
    assert record.held_at is not None
    assert not outcomes.finalize_success(
        task_id=accepted.id, attempt_id=claimed.attempt_id, result=99, database_alias=DB
    )


def test_workflow_recovery_advances_after_lost_callback(backend, monkeypatch):
    from django_tasks_aws.workflows import Workflow, step
    from django_tasks_aws.workflows.models import NodeStatus, WorkflowNode
    from django_tasks_aws.workflows.services import reconcile_workflows

    monkeypatch.setattr(dispatch, "publish_task", lambda *args, **kwargs: DispatchResult.DISPATCHED)
    run = Workflow(step(add, 1, 2) >> step(add, 3, 4)).enqueue()
    nodes = WorkflowNode.objects.filter(run_id=run.pk).order_by("position")
    first = nodes.first()
    assert first.status == NodeStatus.READY
    TaskRecord.objects.filter(pk=first.task_id).update(
        status=TaskStatus.SUCCESSFUL, result=3, finished_at=timezone.now()
    )
    assert reconcile_workflows(database_alias=DB, backend_alias="default") == 1
    assert list(nodes.values_list("status", flat=True)) == [NodeStatus.SUCCESSFUL, NodeStatus.READY]
