"""Verify the package imports and exposes its reserved public entry points."""

import pytest

import django_tasks_aws
from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.exceptions import ConfigurationError


def test_version_is_declared() -> None:
    assert django_tasks_aws.__version__ == "0.1.0"


def test_backend_is_lazily_exported() -> None:
    assert django_tasks_aws.DatabaseSqsBackend is DatabaseSqsBackend


def test_backend_rejects_config_without_workers() -> None:
    with pytest.raises(ConfigurationError):
        DatabaseSqsBackend(alias="default", params={"QUEUES": ["short"]})


def test_worker_handler_is_importable() -> None:
    from django_tasks_aws.handlers.worker import lambda_handler

    assert callable(lambda_handler)


def test_sweeper_handler_is_importable() -> None:
    from django_tasks_aws.handlers.sweeper import lambda_handler

    assert callable(lambda_handler)


@pytest.mark.parametrize("workflows", [False, True])
def test_package_initializes_without_web_admin(workflows):
    import subprocess
    import sys

    apps = ["django_tasks_aws"]
    if workflows:
        apps.append("django_tasks_aws.workflows")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
from django.conf import settings
settings.configure(INSTALLED_APPS={apps!r}, SECRET_KEY="test",
    DATABASES={{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}}})
import django
django.setup()
from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.handlers.worker import lambda_handler
print("ready without admin")
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ready without admin" in result.stdout
