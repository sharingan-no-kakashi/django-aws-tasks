"""Spawned processes each initialize Django and open an independent database session."""

import os


def initialize(database, barrier):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    import django

    django.setup()
    from django.db import connections

    connections.close_all()
    connections.databases["postgres"].update(database)
    global ready
    ready = barrier


def race_claim(task_id):
    from django.db import connections

    from django_tasks_aws.services.claims import claim_task

    ready.wait(timeout=20)
    try:
        claim = claim_task(
            task_id=task_id,
            queue_name="short",
            worker_name="short",
            lease_seconds=90,
            database_alias="postgres",
        )
        return str(claim.attempt_id) if claim else None
    finally:
        connections.close_all()


def race_finalize_or_reap(options):
    from django.db import connections

    from django_tasks_aws import policies
    from django_tasks_aws.services import outcomes, reconcile

    action, task_id, token = options
    ready.wait(timeout=20)
    try:
        if action == "finalize":
            return outcomes.finalize_success(
                task_id=task_id, attempt_id=token, result=3, database_alias="postgres"
            )
        catalog = policies.build_policy_catalog(
            max_attempts=5, allowed_routes=("short",), overrides=None
        )
        return reconcile.recover_expired_attempts(
            database_alias="postgres", policy_catalog=catalog, limit=10
        )
    finally:
        connections.close_all()


def race_publish_or_claim(options):
    from django.db import connections

    from django_tasks_aws.schemas import load_route_manifest
    from django_tasks_aws.services import dispatch
    from tests.conftest import make_manifest
    from tests.support.fake_sqs import FakeSqsTransport

    action, task_id = options
    if action == "claim":
        return race_claim(task_id)
    ready.wait(timeout=20)
    try:
        transport = FakeSqsTransport()
        result = dispatch.publish_task(
            task_id,
            database_alias="postgres",
            routes=load_route_manifest(make_manifest()),
            transport=transport,
        )
        return result.value, len(transport.sent)
    finally:
        connections.close_all()


def race_replay(task_id):
    from django.db import connections
    from django.tasks import default_task_backend

    from django_tasks_aws.services import control, dispatch
    from tests.support.fake_sqs import FakeSqsTransport

    dispatch.set_transport_for_testing(FakeSqsTransport())
    request = control.ControlRequest(
        "process-replay", "provider repaired", "process-operator", effects_reviewed=True
    )
    ready.wait(timeout=20)
    try:
        result = control.operate(
            backend=default_task_backend, task_id=task_id, action="replay", request=request
        )
        return str(result.replacement_task_id)
    finally:
        connections.close_all()


def race_execute(task_id):
    from django.db import connections
    from django.tasks import default_task_backend

    from django_tasks_aws.schemas import WorkerIdentity
    from django_tasks_aws.services.worker import handle_task

    ready.wait(timeout=20)
    try:
        return handle_task(
            task_id,
            backend=default_task_backend,
            worker=WorkerIdentity("short", ""),
            remaining_seconds=60,
        ).value
    finally:
        connections.close_all()
