"""Token-owned publication reservations."""

import uuid

import pytest

from django_tasks_aws import serialization
from django_tasks_aws.models import TaskStatus
from django_tasks_aws.services import dispatch
from django_tasks_aws.services.dispatch import DispatchResult
from tests.support.factories import make_task_record
from tests.support.fake_sqs import ResponseLost, SendMessageFailure

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_publish_dispatches_and_confirms(fake_transport, manifest):
    record = make_task_record()
    result = dispatch.publish_task(
        record.id, transport=fake_transport, routes=manifest, database_alias="postgres"
    )

    assert result is DispatchResult.DISPATCHED
    record.refresh_from_db()
    assert record.last_dispatched_at is not None
    assert record.dispatch_token is None
    assert record.dispatches_without_claim == 1
    assert fake_transport.sent == [
        serialization.dumps(serialization.serialize_envelope(str(record.id)))
    ]


def test_publish_held_task_is_not_eligible(fake_transport, manifest):
    from django.utils import timezone

    from django_tasks_aws.models import HoldReason

    record = make_task_record(
        held_at=timezone.now(),
        hold_reason=HoldReason.AMBIGUOUS_OUTCOME,
    )
    result = dispatch.publish_task(
        record.id, transport=fake_transport, routes=manifest, database_alias="postgres"
    )

    assert result is DispatchResult.NOT_ELIGIBLE
    assert fake_transport.sent == []


def test_publish_terminal_task_is_not_eligible(fake_transport, manifest):
    from django.utils import timezone

    record = make_task_record(status=TaskStatus.SUCCESSFUL, finished_at=timezone.now())
    result = dispatch.publish_task(
        record.id, transport=fake_transport, routes=manifest, database_alias="postgres"
    )

    assert result is DispatchResult.NOT_ELIGIBLE
    assert fake_transport.sent == []


def test_publish_failure_clears_reservation_and_backs_off(manifest):
    from tests.support.fake_sqs import FakeSqsTransport

    record = make_task_record()
    transport = FakeSqsTransport(fail_next_sends=1)

    with pytest.raises(SendMessageFailure):
        dispatch.publish_task(
            record.id, transport=transport, routes=manifest, database_alias="postgres"
        )

    record.refresh_from_db()
    assert record.dispatch_token is None
    assert record.next_dispatch_at > record.available_at


def test_publication_backoff_is_jittered_within_bounds():
    from django_tasks_aws.services.dispatch import compute_publication_backoff
    from tests.support.randomness import seeded_random

    with seeded_random(0):
        values = [compute_publication_backoff() for _ in range(50)]
    assert all(15 <= value <= 45 for value in values)
    assert len(set(values)) > 1


def test_publish_lost_response_clears_reservation(manifest):
    from tests.support.fake_sqs import FakeSqsTransport

    record = make_task_record()
    transport = FakeSqsTransport(lose_response_once=True)

    with pytest.raises(ResponseLost):
        dispatch.publish_task(
            record.id, transport=transport, routes=manifest, database_alias="postgres"
        )

    record.refresh_from_db()
    assert record.dispatch_token is None


def test_stale_token_cannot_confirm_after_claim(manifest):
    """A publisher whose reservation is superseded by a claim is a no-op."""
    from django.db import connections

    from django_tasks_aws.services import claims

    record = make_task_record()
    token = uuid.uuid4()

    with connections["postgres"].cursor() as cursor:
        dispatch.reserve(
            cursor,
            token=token,
            task_id=record.id,
            queue_name="short",
            reservation_seconds=15,
            dispatch_limit=12,
        )

    claimed = claims.claim_task(
        task_id=record.id,
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    assert claimed is not None

    with connections["postgres"].cursor() as cursor:
        confirmed = dispatch.confirm(cursor, token=token, task_id=record.id, redispatch_seconds=300)
    assert confirmed is False
