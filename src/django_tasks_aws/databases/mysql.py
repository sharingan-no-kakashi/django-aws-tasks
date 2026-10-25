"""MySQL/InnoDB coordination with scoped session limits and a real wall clock."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import transaction
from django.db.backends.base.base import BaseDatabaseWrapper

from django_tasks_aws.exceptions import ConfigurationError


class MySQLAdapter:
    clock_sql = "SYSDATE(6)"

    def require_writer(self, connection: BaseDatabaseWrapper) -> None:
        connection.ensure_connection()
        if connection.mysql_is_mariadb or connection.mysql_version < (8, 4):  # type: ignore[attr-defined]
            raise ConfigurationError("Task coordination requires MySQL 8.4+ (not MariaDB)")
        self.require_writable_session(connection)
        self.require_utc_clock(connection)
        self.require_transactional_tables(connection)
        self.require_wall_clock(connection)

    def require_writable_session(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT @@global.read_only, @@global.super_read_only, "
                "@@session.transaction_read_only, @@session.transaction_isolation, "
                "@@session.default_storage_engine, @@session.sql_mode"
            )
            read_only, super_read_only, transaction_read_only, isolation, engine, mode = (
                cursor.fetchone()
            )
        if read_only or super_read_only or transaction_read_only:
            raise ConfigurationError("Task coordination requires a writable MySQL primary")
        if isolation != "READ-COMMITTED" or engine.upper() != "INNODB":
            raise ConfigurationError("MySQL coordination requires InnoDB and READ COMMITTED")
        if not {"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"}.intersection(mode.split(",")):
            raise ConfigurationError("MySQL coordination requires strict SQL mode")

    def require_utc_clock(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT @@session.time_zone, @@system_time_zone, "
                "@@global.log_bin, @@session.binlog_format"
            )
            session_zone, system_zone, log_bin, binlog_format = cursor.fetchone()
        utc_session = session_zone in {"+00:00", "UTC", "Etc/UTC"}
        utc_system = session_zone == "SYSTEM" and system_zone in {"UTC", "GMT"}
        if connection.timezone_name != "UTC" or not (utc_session or utc_system):
            raise ConfigurationError(
                "MySQL coordination requires Django and session time zones in UTC"
            )
        if log_bin and binlog_format != "ROW":
            raise ConfigurationError("MySQL coordination requires row-based binary logging")

    def require_transactional_tables(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() "
                "AND table_name IN ('task_record', 'task_attempt', 'task_control_event') "
                "AND engine <> 'InnoDB'"
            )
            if cursor.fetchone():
                raise ConfigurationError("All task coordination tables must use InnoDB")

    def require_wall_clock(self, connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT SYSDATE(6), SLEEP(0.001), SYSDATE(6)")
            before, _, after = cursor.fetchone()
        if before >= after:
            raise ConfigurationError("MySQL coordination requires sysdate-is-now disabled")

    @contextmanager
    def transaction(self, connection: BaseDatabaseWrapper) -> Iterator[None]:
        previous = self.read_limits(connection)
        restored = False
        try:
            self.set_limits(connection, (1, 1, 2000))
            with transaction.atomic(using=connection.alias):
                yield
                if not connection.needs_rollback:
                    self.set_limits(connection, previous)
                    restored = True
        finally:
            # Restore after atomic has rolled back a failed transaction/savepoint.
            if not restored:
                self.restore_after_rollback(connection, previous)

    def restore_after_rollback(self, connection, previous):
        if connection.connection is not None and not connection.needs_rollback:
            self.set_limits(connection, previous)
        else:
            connection.close()

    def read_limits(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT @@session.innodb_lock_wait_timeout, "
                "@@session.lock_wait_timeout, @@session.max_execution_time"
            )
            return cursor.fetchone()

    def set_limits(self, connection, limits):
        with connection.cursor() as cursor:
            cursor.execute(
                "SET SESSION innodb_lock_wait_timeout = %s, "
                "SESSION lock_wait_timeout = %s, SESSION max_execution_time = %s",
                limits,
            )
