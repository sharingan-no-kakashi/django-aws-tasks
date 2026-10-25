"""Minimal Django settings for the library test suite.

Unit tests use an in-memory SQLite ``default`` alias. Integration tests that
exercise real PostgreSQL behavior connect through the ``postgres`` alias (or the
raw helpers in ``tests.support.postgres``) and are marked ``postgres``.
"""

import os

SECRET_KEY = "test"

USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_tasks_aws",
    "django_tasks_aws.workflows",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
    "postgres": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "django_tasks_aws_test"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "TEST": {"DEPENDENCIES": []},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

_WORKER_PROFILE = {
    "source_queue_arn": "arn:aws:sqs:us-east-1:123:queue",
    "source_queue_url": "https://sqs.us-east-1.amazonaws.com/123/queue",
    "timeout_seconds": 60,
    "lease_seconds": 90,
    "visibility_timeout_seconds": 360,
    "max_receive_count": 5,
    "concurrency": 10,
    "dispatch_limit": 12,
    "redispatch_seconds": 300,
}

TASKS = {
    "default": {
        "BACKEND": "django_tasks_aws.backends.DatabaseSqsBackend",
        "QUEUES": ["short", "documents"],
        "OPTIONS": {
            "database_alias": "postgres",
            "workers": {
                name: dict(
                    _WORKER_PROFILE,
                    source_queue_arn=f"arn:aws:sqs:us-east-1:123:{name}",
                    source_queue_url=f"https://sqs.us-east-1.amazonaws.com/123/{name}",
                )
                for name in ("short", "documents")
            },
            "allowed_task_modules": ["tests.demo_tasks"],
        },
    }
}

ROOT_URLCONF = "tests.admin_urls"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
STATIC_URL = "static/"
