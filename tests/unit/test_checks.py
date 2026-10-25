"""Django system checks for worker configuration."""

from django.test import override_settings

from django_tasks_aws.checks import check_worker_configuration
from tests.unit.test_schemas import valid_profile

BACKEND = "django_tasks_aws.backends.DatabaseSqsBackend"


def backend_config(**options_overrides) -> dict:
    options = {
        "database_alias": "postgres",
        "workers": {
            "short": valid_profile(),
            "documents": valid_profile(
                source_queue_arn="arn:aws:sqs:us-east-1:123:documents",
                source_queue_url="https://sqs.us-east-1.amazonaws.com/123/documents",
            ),
        },
        "allowed_task_modules": ["sample_app.tasks"],
    }
    options.update(options_overrides)
    return {"BACKEND": BACKEND, "QUEUES": ["short", "documents"], "OPTIONS": options}


def ids(messages) -> list[str]:
    return [m.id for m in messages]


@override_settings()
def test_valid_configuration_passes() -> None:
    with override_settings(TASKS={"default": backend_config()}):
        assert check_worker_configuration(None) == []


@override_settings()
def test_missing_workers_reports_e001() -> None:
    with override_settings(TASKS={"default": backend_config(workers={})}):
        assert ids(check_worker_configuration(None)) == ["django_tasks_aws.E001"]


@override_settings()
def test_unsafe_profile_reports_e002() -> None:
    workers = {"short": valid_profile(lease_seconds=30)}
    with override_settings(TASKS={"default": backend_config(workers=workers)}):
        assert ids(check_worker_configuration(None)) == ["django_tasks_aws.E002"]


@override_settings()
def test_mismatched_routes_reported() -> None:
    config = backend_config()
    config["QUEUES"] = ["short"]
    with override_settings(TASKS={"default": config}):
        messages = check_worker_configuration(None)
        assert "django_tasks_aws.E004" in ids(messages)
