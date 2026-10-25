"""Read-only protocol views and reviewed recovery actions for any AdminSite."""

import json
from dataclasses import asdict
from typing import cast
from uuid import uuid4

from django import forms
from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.tasks import task_backends
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.http import urlencode

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.exceptions import DjangoTasksAWSError
from django_tasks_aws.models import TaskAttempt, TaskControlEvent, TaskRecord
from django_tasks_aws.services import inspection
from django_tasks_aws.services.control import ControlRequest, operate

SIGNING_SALT = "django_tasks_aws.admin.recovery.v1"


class ReviewForm(forms.Form):
    reason = forms.CharField(max_length=2000, widget=forms.Textarea)
    request_id = forms.CharField(max_length=200, initial=uuid4)
    queue_name = forms.CharField(required=False, label="Replay destination (optional)")
    effects_reviewed = forms.BooleanField(
        required=False, label="I reviewed possible business effects"
    )
    reset_delivery = forms.BooleanField(required=False, label="Reset the delivery budget")
    selector = forms.CharField(widget=forms.HiddenInput)


class ReadOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


class TaskRecordAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "task_name",
        "queue_name",
        "status",
        "diagnostic_category",
        "attempts_started",
        "hold_reason",
        "created_at",
    )
    list_filter = ("status", "queue_name", "hold_reason", "task_name", "created_at")
    search_fields = ("task_name", "operation_key", "dedupe_key")
    list_per_page = 50
    actions = ("dispatch_selected", "release_selected", "replay_selected", "recover_selected")  # type: ignore[assignment]
    change_list_template = "django_tasks_aws/change_list.html"

    def get_queryset(self, request):
        return inspection.get_annotated_tasks(self.get_backend(request)).defer(
            "payload", "result", "last_error"
        )

    def get_search_results(self, request, queryset, search_term):
        return (
            (inspection.search_tasks(queryset, search_term), False)
            if search_term
            else (queryset, False)
        )

    def get_backend(self, request):
        return cast(DatabaseSqsBackend, task_backends[request.GET.get("backend", "default")])

    @admin.display(description="Condition")
    def diagnostic_category(self, obj):
        return obj.category

    def has_control_permission(self, request):
        return request.user.has_perm("django_tasks_aws.control_taskrecord")

    def get_urls(self):
        view = self.admin_site.admin_view
        urls = [
            path("problems/", view(self.find_problems), name="django_tasks_aws_problems"),
            path("workers/", view(self.workers), name="django_tasks_aws_workers"),
            path("review/", view(self.review), name="django_tasks_aws_review"),
        ]
        return urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(
            request,
            extra_context={
                **(extra_context or {}),
                "admin_namespace": self.admin_site.name,
            },
        )

    def require_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied

    def render(self, request, template, **context):
        return TemplateResponse(
            request,
            f"django_tasks_aws/{template}.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "admin_namespace": self.admin_site.name,
                **context,
            },
        )

    def find_problems(self, request):
        self.require_view(request)
        try:
            page = inspection.list_tasks(
                self.get_backend(request),
                problematic=True,
                queue=request.GET.get("queue"),
                category=request.GET.get("category"),
                search=request.GET.get("q"),
                offset=int(request.GET.get("offset", 0)),
            )
        except (ValueError, DjangoTasksAWSError) as error:
            return HttpResponseBadRequest(str(error))
        return self.render(
            request,
            "problems",
            title="Problem tasks",
            page=page,
            backend_alias=self.get_backend(request).alias,
        )

    def workers(self, request):
        self.require_view(request)
        return self.render(
            request,
            "workers",
            title="Worker routes",
            workers=inspection.get_worker_summaries(self.get_backend(request)),
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        self.require_view(request)
        sensitive = request.user.has_perm("django_tasks_aws.view_task_payload")
        try:
            detail = inspection.get_task_detail(
                self.get_backend(request), object_id, sensitive=sensitive
            )
        except (ValueError, DjangoTasksAWSError) as error:
            return HttpResponseBadRequest(str(error))
        return self.render(
            request,
            "detail",
            title=f"Task {object_id}",
            detail=detail,
            detail_json=json.dumps(detail, cls=DjangoJSONEncoder, indent=2),
            can_control=self.has_control_permission(request),
            backend_alias=self.get_backend(request).alias,
        )

    def review(self, request):
        self.require_view(request)
        if not self.has_control_permission(request):
            raise PermissionDenied
        try:
            if request.method == "POST" and request.POST.get("confirmation"):
                return self.confirm(request)
            return self.preview_action(request)
        except (
            signing.BadSignature,
            ValueError,
            KeyError,
            ValidationError,
            DjangoTasksAWSError,
        ) as error:
            return HttpResponseBadRequest(str(error))

    def preview_action(self, request):
        selector = self.selection_filter(request)
        form = ReviewForm(request.POST or None, initial={"selector": selector})
        rows = []
        confirmation = None
        if request.method == "POST" and form.is_valid():
            data = signing.loads(selector, salt=SIGNING_SALT, max_age=900)
            data["options"] = {
                key: value for key, value in form.cleaned_data.items() if key != "selector"
            }
            rows = self.operate_selection(request, data, dry_run=True)
            if not any("error" in row for row in rows):
                confirmation = signing.dumps(data, salt=SIGNING_SALT)
        return self.render(
            request,
            "review",
            title="Review task recovery",
            form=form,
            rows=rows,
            confirmation=confirmation,
        )

    def selection_filter(self, request):
        if request.method == "POST":
            return request.POST["selector"]
        identities = request.GET.get("ids", "").split(",")
        if not 1 <= len(identities) <= 100 or not all(identities):
            raise ValueError("Select 1–100 task identities")
        backend = self.get_backend(request)
        holds = {
            str(row["id"]): row["held_at"].isoformat() if row["held_at"] else None
            for row in inspection.get_annotated_tasks(backend)
            .filter(id__in=identities)
            .values("id", "held_at")
        }
        return signing.dumps(
            {
                "ids": identities,
                "action": request.GET["action"],
                "backend": backend.alias,
                "holds": holds,
            },
            salt=SIGNING_SALT,
        )

    def confirm(self, request):
        data = signing.loads(request.POST["confirmation"], salt=SIGNING_SALT, max_age=900)
        rows = self.operate_selection(request, data, dry_run=False)
        if any("error" in row for row in rows):
            self.message_user(
                request, "Some targets were rejected; inspect each result.", messages.ERROR
            )
        else:
            self.message_user(
                request, "Recovery requests accepted; business completion is separate."
            )
        return self.render(request, "review", title="Recovery results", rows=rows)

    def operate_selection(self, request, data, *, dry_run):
        backend = cast(DatabaseSqsBackend, task_backends[data["backend"]])
        options = data["options"]
        rows = []
        for task_id in data["ids"]:
            control = ControlRequest(
                **options,
                actor_kind="user",
                origin="admin",
                actor_reference=str(request.user.pk),
                expected_hold=data["holds"].get(task_id),
            )
            try:
                rows.append(
                    asdict(
                        operate(
                            backend=backend,
                            task_id=task_id,
                            action=data["action"],
                            request=control,
                            dry_run=dry_run,
                        )
                    )
                )
                if dry_run:
                    state = TaskRecord.objects.using(backend.database_alias).get(pk=task_id)
                    rows[-1].update(
                        task_name=state.task_name,
                        operation_key=state.operation_key,
                        queue_name=options.get("queue_name") or state.queue_name,
                        status=state.status,
                        reset_delivery=options.get("reset_delivery"),
                        uncertainty=(state.last_error or {}).get("uncertain", "unknown"),
                    )
            except (DjangoTasksAWSError, DatabaseError, ValueError) as error:
                rows.append({"task_id": task_id, "error": str(error)})
        return rows

    def selected(self, request, queryset, action):
        identities = list(queryset.values_list("id", flat=True)[:101])
        url = reverse(f"{self.admin_site.name}:django_tasks_aws_review")
        return HttpResponseRedirect(
            url
            + "?"
            + urlencode(
                {
                    "ids": ",".join(map(str, identities)),
                    "action": action,
                    "backend": self.get_backend(request).alias,
                }
            )
        )

    @admin.action(description="Review eligible dispatch", permissions=["control"])
    def dispatch_selected(self, request, queryset):
        return self.selected(request, queryset, "dispatch")

    @admin.action(description="Review hold release", permissions=["control"])
    def release_selected(self, request, queryset):
        return self.selected(request, queryset, "release")

    @admin.action(description="Review terminal replay", permissions=["control"])
    def replay_selected(self, request, queryset):
        return self.selected(request, queryset, "replay")

    @admin.action(description="Review expired-attempt recovery", permissions=["control"])
    def recover_selected(self, request, queryset):
        return self.selected(request, queryset, "reconcile")


class TaskAttemptAdmin(ReadOnlyAdmin):
    list_display = ("id", "task_id", "attempt_number", "worker_name", "status", "outcome")
    list_filter = ("worker_name", "status", "outcome")
    search_fields = ("lambda_request_id", "sqs_message_id")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .using(cast(DatabaseSqsBackend, task_backends["default"]).database_alias)
        )

    def get_fields(self, request, obj=None):
        hidden = (
            set()
            if request.user.has_perm("django_tasks_aws.view_task_payload")
            else {"error_message", "traceback"}
        )
        return [field.name for field in self.model._meta.fields if field.name not in hidden]


class TaskControlEventAdmin(ReadOnlyAdmin):
    list_display = ("id", "action", "task_id", "request_id", "actor_reference", "created_at")
    list_filter = ("action", "origin")
    search_fields = ("request_id", "actor_reference")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .using(cast(DatabaseSqsBackend, task_backends["default"]).database_alias)
        )


def register_admin(site):
    for model, model_admin in (
        (TaskRecord, TaskRecordAdmin),
        (TaskAttempt, TaskAttemptAdmin),
        (TaskControlEvent, TaskControlEventAdmin),
    ):
        if not site.is_registered(model):
            site.register(model, model_admin)


register_admin(admin.site)
