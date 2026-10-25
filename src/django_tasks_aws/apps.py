"""Django app configuration for ``django_tasks_aws``."""

from django.apps import AppConfig


class DjangoTasksAwsConfig(AppConfig):
    """Installs the package's models, checks, admin, and migrations."""

    name = "django_tasks_aws"
    verbose_name = "Django Tasks (AWS)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django_tasks_aws import checks  # noqa: F401  (registers system checks)
