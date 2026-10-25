from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from django_tasks_aws.models import HoldReason, TaskControlEvent, TaskRecord, TaskStatus
from django_tasks_aws.services import dispatch
from tests.support.factories import make_task_record

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(databases=["default", "postgres"], transaction=True),
]


@pytest.fixture
def staff():
    user = get_user_model().objects.create_user("viewer", password="test", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_taskrecord"))
    return user


@pytest.fixture
def operator(staff):
    staff.user_permissions.add(Permission.objects.get(codename="control_taskrecord"))
    return staff


@pytest.fixture(autouse=True)
def no_send(monkeypatch):
    sender = Mock()
    monkeypatch.setattr(dispatch, "publish_task", sender)
    return sender


def review(client, record, *, namespace="admin", action="replay"):
    url = reverse(f"{namespace}:django_tasks_aws_review")
    response = client.get(url, {"ids": str(record.id), "action": action})
    assert response.status_code == 200
    form = response.context["form"]
    response = client.post(
        url,
        {
            "selector": form["selector"].value(),
            "reason": "effects investigated",
            "request_id": "browser-request",
            "effects_reviewed": "on",
            "reset_delivery": "on",
        },
    )
    assert response.status_code == 200
    return url, response.context["confirmation"]


def test_read_only_staff_sees_diagnostics_but_not_sensitive_fields_or_actions(client, staff):
    record = make_task_record(payload={"args": ["supersecret"], "kwargs": {}})
    client.force_login(staff)
    response = client.get(reverse("admin:django_tasks_aws_taskrecord_change", args=[record.id]))
    assert response.status_code == 200
    assert b"supersecret" not in response.content and b"Review replay" not in response.content
    response = client.get(
        reverse("admin:django_tasks_aws_review"), {"ids": record.id, "action": "replay"}
    )
    assert response.status_code == 403


def test_review_requires_preview_then_confirmation_and_is_idempotent(client, operator, no_send):
    record = make_task_record(
        status=TaskStatus.FAILED, finished_at=timezone.now(), operation_key="effect:42"
    )
    client.force_login(operator)
    url, token = review(client, record)
    assert token and TaskControlEvent.objects.using("postgres").count() == 0
    response = client.post(url, {"confirmation": token})
    assert response.status_code == 200
    assert client.post(url, {"confirmation": token}).status_code == 200
    replacement = TaskRecord.objects.using("postgres").get(replay_of_id=record.id)
    assert replacement.operation_key == "effect:42"
    assert TaskControlEvent.objects.using("postgres").count() == 1
    assert no_send.call_count == 1


def test_preview_state_change_is_revalidated(client, operator):
    record = make_task_record(held_at=timezone.now(), hold_reason=HoldReason.AMBIGUOUS_OUTCOME)
    client.force_login(operator)
    url, token = review(client, record, action="release")
    TaskRecord.objects.using("postgres").filter(pk=record.id).update(
        held_at=timezone.now() + timedelta(seconds=1)
    )
    response = client.post(url, {"confirmation": token})
    assert b"hold changed" in response.content
    assert TaskControlEvent.objects.using("postgres").count() == 0


def test_csrf_blocks_confirmation(operator):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    response = client.post(reverse("admin:django_tasks_aws_review"), {"confirmation": "invalid"})
    assert response.status_code == 403


def test_signed_confirmation_cannot_be_tampered_with(client, operator):
    client.force_login(operator)
    assert (
        client.post(reverse("admin:django_tasks_aws_review"), {"confirmation": "bad"}).status_code
        == 400
    )


def test_admin_escapes_diagnostics_and_has_no_raw_mutation_forms(client, operator):
    operator.user_permissions.add(Permission.objects.get(codename="view_task_payload"))
    record = make_task_record(last_error={"message": "<script>alert(1)</script>"})
    client.force_login(operator)
    response = client.get(reverse("admin:django_tasks_aws_taskrecord_change", args=[record.id]))
    assert b"<script>alert(1)</script>" not in response.content
    assert b"&lt;script&gt;" in response.content
    assert client.get(reverse("admin:django_tasks_aws_taskrecord_add")).status_code == 403
    assert (
        client.post(
            reverse("admin:django_tasks_aws_taskrecord_delete", args=[record.id])
        ).status_code
        == 403
    )


def test_problem_and_worker_views_and_custom_site(client, staff):
    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    client.force_login(staff)
    for namespace in ("admin", "custom"):
        response = client.get(reverse(f"{namespace}:django_tasks_aws_problems"))
        assert response.status_code == 200 and str(record.id).encode() in response.content
        assert client.get(reverse(f"{namespace}:django_tasks_aws_workers")).status_code == 200
        assert (
            client.get(reverse(f"{namespace}:django_tasks_aws_taskrecord_changelist")).status_code
            == 200
        )


def test_custom_site_recovery_uses_its_own_namespaces(client, operator):
    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    client.force_login(operator)
    url, token = review(client, record, namespace="custom")
    response = client.post(url, {"confirmation": token})
    assert response.status_code == 200 and b"/custom/" in response.content


def test_task_and_attempt_uuid_search_returns_one_matching_task(client, staff):
    from django_tasks_aws.services.claims import claim_task

    first, second = make_task_record(), make_task_record()
    claimed = claim_task(
        task_id=str(first.id),
        queue_name="short",
        worker_name="short",
        lease_seconds=90,
        database_alias="postgres",
    )
    client.force_login(staff)
    for identity in (first.id, claimed.attempt_id):
        response = client.get(
            reverse("admin:django_tasks_aws_taskrecord_changelist"), {"q": str(identity)}
        )
        assert response.status_code == 200
        assert {row.id for row in response.context["cl"].result_list} == {first.id}
    assert second.id != first.id


def test_admin_preview_describes_preserved_effect_and_destination(client, operator):
    record = make_task_record(
        status=TaskStatus.FAILED, finished_at=timezone.now(), operation_key="invoice:42"
    )
    client.force_login(operator)
    url, token = review(client, record)
    assert token
    response = client.get(url, {"ids": record.id, "action": "replay"})
    selector = response.context["form"]["selector"].value()
    response = client.post(
        url,
        {
            "selector": selector,
            "reason": "checked effects",
            "request_id": "concrete-preview",
            "effects_reviewed": "on",
        },
    )
    assert b"invoice:42" in response.content and b"Route short" in response.content
    assert b"Current state FAILED" in response.content
