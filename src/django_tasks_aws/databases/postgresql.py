"""PostgreSQL primary validation and transaction-local coordination limits."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import transaction
from django.db.backends.base.base import BaseDatabaseWrapper

from django_tasks_aws.exceptions import ConfigurationError


class PostgresAdapter:
    clock_sql = "CLOCK_TIMESTAMP()"

    def require_writer(self, connection: BaseDatabaseWrapper) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_is_in_recovery(), current_setting('transaction_read_only')")
            recovery, read_only = cursor.fetchone()
        if connection.pg_version < 150000 or recovery or read_only == "on":  # type: ignore[attr-defined]
            raise ConfigurationError("Task coordination requires a writable PostgreSQL 15+ primary")

    @contextmanager
    def transaction(self, connection: BaseDatabaseWrapper) -> Iterator[None]:
        with transaction.atomic(using=connection.alias):
            previous = self.set_limits(connection)
            yield
            # On failure atomic rolls back SET LOCAL, including at nested savepoints.
            if not connection.needs_rollback:
                self.restore_limits(connection, previous)

    def set_limits(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')"
            )
            previous = cursor.fetchone()
            cursor.execute("SET LOCAL lock_timeout = '1s'")
            cursor.execute("SET LOCAL statement_timeout = '2s'")
        return previous

    def restore_limits(self, connection, previous):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [previous[0]])
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", [previous[1]])
