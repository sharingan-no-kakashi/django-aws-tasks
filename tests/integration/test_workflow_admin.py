"""Workflow admin inspection and reviewed recovery."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from django_tasks_aws.workflows import Workflow, step
from django_tasks_aws.workflows.models import RunStatus
from tests.demo_tasks import add

DB = "postgres"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.django_db(databases=["default", "postgres"], transaction=True),
]


@pytest.fixture
def staff():
    user = get_user_model().objects.create_user("wf-viewer", password="test", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_workflowrun"))
    return user


@pytest.fixture
def operator(staff):
    staff.user_permissions.add(Permission.objects.get(codename="control_taskrecord"))
    return staff


@pytest.fixture
def client():
    return Client()


def test_read_only_staff_views_workflow_detail_without_sensitive_data(client, staff):
    run = Workflow(step(add, 1, 1).named("first") >> step(add, 2, 2), name="pipeline").enqueue()
    client.force_login(staff)

    response = client.get(reverse("admin:django_tasks_workflows_workflowrun_change", args=[run.pk]))

    assert response.status_code == 200
    assert b"first" in response.content
    assert b'"args"' not in response.content
    assert b"Cancel workflow" not in response.content


def test_workflow_review_requires_control_permission(client, staff):
    run = Workflow(step(add, 1, 1)).enqueue()
    client.force_login(staff)

    response = client.get(
        reverse("admin:django_tasks_workflows_review", args=[run.pk]), {"action": "cancel"}
    )

    assert response.status_code == 403


def test_operator_cancels_workflow_via_review(client, operator):
    run = Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    client.force_login(operator)
    url = reverse("admin:django_tasks_workflows_review", args=[run.pk])

    response = client.get(url, {"action": "cancel"})
    assert response.status_code == 200

    response = client.post(
        url, {"action": "cancel", "reason": "stop", "request_id": "browser-cancel"}
    )
    assert response.status_code == 200

    run.refresh_from_db()
    assert run.status == RunStatus.CANCELLED


@pytest.mark.parametrize("custom", [False, True])
def test_protocol_models_are_immutable_on_all_admin_sites(operator, custom):
    from django.contrib import admin
    from django.test import RequestFactory

    from django_tasks_aws.workflows.admin import register_workflow_admin
    from django_tasks_aws.workflows.models import WorkflowAction, WorkflowNode, WorkflowRun

    site = admin.AdminSite(name="workflow_custom") if custom else admin.site
    register_workflow_admin(site)
    request = RequestFactory().get("/")
    request.user = operator
    for model in (WorkflowRun, WorkflowNode, WorkflowAction):
        model_admin = site._registry[model]
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)
        assert not model_admin.get_actions(request)


def test_node_and_audit_details_require_sensitive_permission(staff):
    from django.contrib import admin
    from django.test import RequestFactory

    from django_tasks_aws.workflows.models import WorkflowAction, WorkflowNode

    request = RequestFactory().get("/")
    request.user = staff
    for model, sensitive in (
        (WorkflowNode, {"arguments", "error", "task_config"}),
        (WorkflowAction, {"reason", "actor_reference"}),
    ):
        model_admin = admin.site._registry[model]
        fields = set(model_admin.get_readonly_fields(request))
        assert fields.isdisjoint(sensitive)
        assert sensitive.issubset(set(model_admin.get_exclude(request)))


def test_workflow_control_requires_csrf_and_dry_run_is_safe(operator):
    from django_tasks_aws.workflows.models import WorkflowAction

    run = Workflow(step(add, 1, 1)).enqueue()
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    url = reverse("admin:django_tasks_workflows_review", args=[run.pk])
    data = dict(action="cancel", reason="preview", request_id="csrf", dry_run="on")
    assert client.post(url, data).status_code == 403
    response = client.get(url, {"action": "cancel"})
    assert b"Task identity" in response.content
    data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
    assert client.post(url, data).status_code == 200
    run.refresh_from_db()
    assert run.cancel_requested_at is None
    assert not WorkflowAction.objects.using(DB).filter(run=run).exists()


def test_changed_cancel_intent_is_an_actionable_form_error(client, operator):
    run = Workflow(step(add, 1, 1)).enqueue()
    client.force_login(operator)
    url = reverse("admin:django_tasks_workflows_review", args=[run.pk])
    data = dict(action="cancel", reason="stop", request_id="same")
    assert client.post(url, data).status_code == 200
    data["reason"] = "changed"
    response = client.post(url, data)
    assert response.status_code == 200
    assert b"different intent" in response.content
