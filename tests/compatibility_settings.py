"""Settings for shared contracts on a single explicitly selected SQL engine."""

import os

from tests.settings import *  # noqa: F403

TEST_DATABASE = os.environ.get("TEST_DATABASE", "postgres")
ENGINES = {
    "postgres": "django.db.backends.postgresql",
    "mysql": "django.db.backends.mysql",
    "mariadb": "django.db.backends.mysql",
    "oracle": "django.db.backends.oracle",
}
if TEST_DATABASE not in ENGINES:
    raise ValueError(f"Unsupported TEST_DATABASE: {TEST_DATABASE}")

DATABASES = {
    "default": {
        "ENGINE": ENGINES[TEST_DATABASE],
        "NAME": os.environ.get("TEST_DB_NAME", "django_tasks_aws_test"),
        "USER": os.environ.get("TEST_DB_USER", "postgres"),
        "PASSWORD": os.environ.get("TEST_DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("TEST_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("TEST_DB_PORT", "5432"),
    }
}
if TEST_DATABASE in {"mysql", "mariadb"}:
    DATABASES["default"]["OPTIONS"] = {
        "charset": "utf8mb4",
        "init_command": "SET time_zone = '+00:00'",
        "isolation_level": "read committed",
    }
if TEST_DATABASE == "oracle":
    DATABASES["default"]["TEST"] = {"USER": "test_tasks", "PASSWORD": "TestTasksPassword1"}

TASKS = {  # noqa: F405
    "default": {
        **TASKS["default"],  # noqa: F405
        "OPTIONS": {
            **TASKS["default"]["OPTIONS"],  # noqa: F405
            "database_alias": "default",
            "immediate_dispatch": False,
        },
    }
}
