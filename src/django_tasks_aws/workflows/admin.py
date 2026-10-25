"""Read-only workflow inspection and reviewed retry/cancellation for any AdminSite."""

import json
from dataclasses import asdict
from typing import ClassVar
from uuid import uuid4

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from django.tasks import task_backends
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.http import urlencode

from django_tasks_aws.admin import ReadOnlyAdmin
from django_tasks_aws.backends import DatabaseSqsBackend

from .exceptions import WorkflowError, WorkflowNotFound
from .inspection import get_run_status
from .models import WorkflowAction, WorkflowNode, WorkflowRun
from .services import cancel_workflow, retry_workflow_step


class _ReviewForm(forms.Form):
    reason = forms.CharField(max_length=2000, widget=forms.Textarea)
    request_id = forms.CharField(max_length=200, initial=uuid4)
    effects_reviewed = forms.BooleanField(
        required=False, label="I reviewed possible business effects"
    )
    dry_run = forms.BooleanField(required=False, label="Preview only")


class WorkflowRunAdmin(ReadOnlyAdmin):
    list_display = ("id", "name", "business_reference", "status", "created_at", "finished_at")
    list_filter = ("status", "backend_alias", "created_at")
    search_fields = ("name", "business_reference", "submission_key")
    list_per_page = 50
    actions = None

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.using(self.get_backend(request).database_alias).filter(
            backend_alias=self.get_backend(request).alias
        )

    def get_backend(self, request):
        return task_backends[request.GET.get("backend", "default")]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_control_permission(self, request):
        return request.user.has_perm("django_tasks_aws.control_taskrecord")

    def get_urls(self):
        urls = [
            path(
                "<path:object_id>/review/",
                self.admin_site.admin_view(self.review),
                name="django_tasks_workflows_review",
            )
        ]
        return urls + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        try:
            snapshot = get_run_status(
                object_id,
                database_alias=self.get_backend(request).database_alias,
                backend_alias=self.get_backend(request).alias,
            )
        except (WorkflowNotFound, WorkflowError) as error:
            return self.render(request, "error", message=str(error), object_id=object_id)
        return self.render(
            request,
            "workflow_detail",
            snapshot=snapshot,
            snapshot_json=json.dumps(asdict(snapshot), cls=DjangoJSONEncoder, indent=2),
            can_control=self.has_control_permission(request),
            object_id=object_id,
            history=WorkflowAction.objects.using(self.get_backend(request).database_alias)
            .filter(run_id=object_id, run__backend_alias=self.get_backend(request).alias)
            .values(
                "action", "request_id", "original_task_id", "replacement_task_id", "created_at"
            )[:100],
        )

    def review(self, request, object_id):
        if not self.has_view_permission(request) or not self.has_control_permission(request):
            raise PermissionDenied
        step_key = request.POST.get("step", request.GET.get("step", ""))
        action = request.POST.get("action", request.GET.get("action", "cancel"))
        backend = self.get_backend(request)
        form = _ReviewForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                rows = self.apply(request, object_id, step_key, action, form.cleaned_data, backend)
            except (WorkflowError, DatabaseError) as error:
                form.add_error(None, str(error))
                return self.render(
                    request,
                    "workflow_review",
                    form=form,
                    action=action,
                    step_key=step_key,
                    object_id=object_id,
                )
            self.message_user(request, "Workflow action completed.", messages.SUCCESS)
            return self.render(request, "workflow_review_result", rows=rows, object_id=object_id)
        try:
            snapshot = get_run_status(
                object_id, database_alias=backend.database_alias, backend_alias=backend.alias
            )
        except WorkflowError as error:
            return self.render(request, "error", message=str(error), object_id=object_id)
        preview = self.preview_action(request, object_id, step_key, action, backend)
        return self.render(
            request,
            "workflow_review",
            form=form,
            action=action,
            step_key=step_key,
            object_id=object_id,
            snapshot=snapshot,
            preview=preview,
        )

    def preview_action(self, request, object_id, step_key, action, backend):
        data = dict(
            request_id=str(uuid4()),
            reason="Eligibility preview",
            effects_reviewed=False,
            dry_run=True,
        )
        try:
            self.apply(request, object_id, step_key, action, data, backend)
            return (
                "Eligible in this snapshot; execution rechecks state and accepted request intent."
            )
        except WorkflowError as error:
            return str(error)

    def apply(self, request, object_id, step_key, action, data, backend):
        if action not in {"retry", "cancel"}:
            raise WorkflowError("Select retry or cancel")
        dry_run = data["dry_run"]
        if action == "retry":
            outcome = retry_workflow_step(
                object_id,
                step_key,
                request_id=data["request_id"],
                reason=data["reason"],
                actor_reference=str(request.user.pk),
                origin="admin",
                actor_kind="user",
                effects_reviewed=data["effects_reviewed"],
                dry_run=dry_run,
                database_alias=backend.database_alias,
                backend_alias=backend.alias,
            )
        else:
            outcome = cancel_workflow(
                object_id,
                request_id=data["request_id"],
                reason=data["reason"],
                actor_reference=str(request.user.pk),
                origin="admin",
                actor_kind="user",
                dry_run=dry_run,
                database_alias=backend.database_alias,
                backend_alias=backend.alias,
            )
        return [asdict(outcome)]

    def render(self, request, template, **context):
        return TemplateResponse(
            request,
            f"django_tasks_aws/{template}.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "admin_namespace": self.admin_site.name,
                "backend_query": urlencode({"backend": self.get_backend(request).alias}),
                **context,
            },
        )


class WorkflowActionAdmin(ReadOnlyAdmin):
    list_display: tuple[str, ...] = ("id", "action", "run_id", "request_id", "created_at")
    list_filter: ClassVar[tuple[str, ...]] = ("action", "origin")
    search_fields: ClassVar[tuple[str, ...]] = ("request_id",)

    def get_queryset(self, request):

        backend = task_backends[request.GET.get("backend", "default")]
        if not isinstance(backend, DatabaseSqsBackend):
            return super().get_queryset(request)
        return (
            super()
            .get_queryset(request)
            .using(backend.database_alias)
            .filter(run__backend_alias=backend.alias)
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        excluded = self.get_exclude(request, obj)
        return tuple(field.name for field in self.model._meta.fields if field.name not in excluded)

    def get_exclude(self, request, obj=None):
        if request.user.has_perm("django_tasks_aws.view_task_payload"):
            return ()
        return ("reason", "actor_reference", "before_summary", "after_summary")


class WorkflowNodeAdmin(WorkflowActionAdmin):
    list_display = ("id", "run_id", "key", "status", "task_id")
    list_filter = ("status",)
    search_fields = ("key", "name")

    def get_exclude(self, request, obj=None):
        if request.user.has_perm("django_tasks_aws.view_task_payload"):
            return ()
        return ("arguments", "error", "task_config")


def register_workflow_admin(site):
    for model, model_admin in (
        (WorkflowRun, WorkflowRunAdmin),
        (WorkflowAction, WorkflowActionAdmin),
        (WorkflowNode, WorkflowNodeAdmin),
    ):
        if not site.is_registered(model):
            site.register(model, model_admin or admin.ModelAdmin)
