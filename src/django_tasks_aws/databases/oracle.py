"""Oracle coordination with UTC wall time and scoped driver round-trip deadlines."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import transaction

from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import ConfigurationError


class OracleAdapter:
    clock_sql = "CAST(SYS_EXTRACT_UTC(SYSTIMESTAMP) AS TIMESTAMP)"

    def require_writer(self, connection):
        connection.ensure_connection()
        if connection.oracle_version < (21,):
            raise ConfigurationError("Task coordination requires Oracle 21c+ (scalar JSON support)")
        if connection.timezone_name != "UTC":
            raise ConfigurationError(
                "Oracle coordination requires Django's connection time zone UTC"
            )
        if connection.settings_dict.get("DISABLE_SERVER_SIDE_CURSORS"):
            raise ConfigurationError("Oracle coordination requires streaming cursors")
        self.require_binary_comparisons(connection)
        self.require_primary(connection)

    @scoped(lambda adapter, connection: adapter.transaction(connection), "self", "connection")
    def require_primary(self, connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'DATABASE_ROLE') FROM DUAL")
            if cursor.fetchone()[0] != "PRIMARY":
                raise ConfigurationError("Task coordination requires an Oracle primary")
            # A zero-row write rejects read-only databases and transactions without
            # requiring access to privileged V$ views or modifying accepted work.
            table = connection.ops.quote_name("task_record")
            column = connection.ops.quote_name("id")
            cursor.execute(f"UPDATE {table} SET {column} = {column} WHERE 1 = 0")

    def require_binary_comparisons(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT value FROM nls_session_parameters "
                "WHERE parameter IN ('NLS_COMP', 'NLS_SORT')"
            )
            if {row[0] for row in cursor.fetchall()} != {"BINARY"}:
                raise ConfigurationError("Oracle coordination requires NLS_COMP/NLS_SORT BINARY")
            cursor.execute(
                "SELECT column_name FROM user_tab_columns "
                "WHERE table_name IN ('TASK_RECORD', 'TASK_ATTEMPT', 'TASK_CONTROL_EVENT') "
                "AND collation NOT IN ('BINARY', 'USING_NLS_COMP')"
            )
            if cursor.fetchone():
                raise ConfigurationError("Oracle task identifiers require binary column collations")

    @contextmanager
    def transaction(self, connection) -> Iterator[None]:
        connection.ensure_connection()
        driver = connection.connection
        previous = driver.call_timeout
        restored = False
        try:
            driver.call_timeout = min(previous or 2000, 2000)
            with transaction.atomic(using=connection.alias):
                yield
                if not connection.needs_rollback:
                    driver.call_timeout = previous
                    restored = True
        finally:
            if not restored:
                if connection.connection is driver and not connection.needs_rollback:
                    driver.call_timeout = previous
                else:
                    connection.close()
