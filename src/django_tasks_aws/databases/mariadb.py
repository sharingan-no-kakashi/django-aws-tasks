"""MariaDB-specific validation and session limits over shared InnoDB coordination."""

from django_tasks_aws.databases.mysql import MySQLAdapter
from django_tasks_aws.exceptions import ConfigurationError


class MariaDBAdapter(MySQLAdapter):
    def require_writer(self, connection):
        connection.ensure_connection()
        if not connection.mysql_is_mariadb or connection.mysql_version < (10, 6):
            raise ConfigurationError("Task coordination requires MariaDB 10.6+")
        self.require_writable_session(connection)
        self.require_utc_clock(connection)
        self.require_transactional_tables(connection)
        self.require_wall_clock(connection)

    def require_writable_session(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT @@global.read_only, @@session.tx_read_only, @@session.tx_isolation, "
                "@@session.default_storage_engine, @@session.sql_mode"
            )
            read_only, transaction_read_only, isolation, engine, mode = cursor.fetchone()
        if read_only or transaction_read_only:
            raise ConfigurationError("Task coordination requires a writable MariaDB primary")
        if isolation != "READ-COMMITTED" or engine.upper() != "INNODB":
            raise ConfigurationError("MariaDB coordination requires InnoDB and READ COMMITTED")
        if not {"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"}.intersection(mode.split(",")):
            raise ConfigurationError("MariaDB coordination requires strict SQL mode")

    def read_limits(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT @@session.innodb_lock_wait_timeout, "
                "@@session.lock_wait_timeout, @@session.max_statement_time"
            )
            lock_wait, metadata_wait, execution_seconds = cursor.fetchone()
        return lock_wait, metadata_wait, execution_seconds * 1000

    def set_limits(self, connection, limits):
        lock_wait, metadata_wait, execution_milliseconds = limits
        with connection.cursor() as cursor:
            cursor.execute(
                "SET SESSION innodb_lock_wait_timeout = %s, "
                "SESSION lock_wait_timeout = %s, SESSION max_statement_time = %s",
                [lock_wait, metadata_wait, execution_milliseconds / 1000],
            )
