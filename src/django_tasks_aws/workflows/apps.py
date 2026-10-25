"""Optional workflow application and admin registration."""

from django.apps import AppConfig, apps


class WorkflowsConfig(AppConfig):
    name = "django_tasks_aws.workflows"
    label = "django_tasks_workflows"
    verbose_name = "Task workflows"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        if apps.is_installed("django.contrib.admin"):
            from django.contrib import admin as django_admin

            from .admin import register_workflow_admin

            register_workflow_admin(django_admin.site)
