"""Real-PostgreSQL helpers for integration tests.

These bypass the Django ORM to demonstrate genuine commit, rollback, and
competing-connection semantics on PostgreSQL — the only acceptable evidence for
locking behavior.
"""

import os

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/django_tasks_aws_test"


def dsn() -> str:
    return os.environ.get("POSTGRES_DSN", DEFAULT_DSN)


def reachable() -> bool:
    try:
        with psycopg.connect(dsn(), connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn(), connect_timeout=3, autocommit=True)
