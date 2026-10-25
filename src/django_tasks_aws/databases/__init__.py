"""Resolve an adapter from the configured Django connection, never from task routing."""

from django.conf import settings
from django.db import connections
from django.db.models import DateTimeField, Func
from django.utils.module_loading import import_string

from django_tasks_aws.databases.base import DatabaseAdapter
from django_tasks_aws.exceptions import ConfigurationError

DEFAULT_ADAPTERS = {
    "postgresql": "django_tasks_aws.databases.postgresql.PostgresAdapter",
    "mysql": "django_tasks_aws.databases.innodb.InnoDBAdapter",
    "mariadb": "django_tasks_aws.databases.mariadb.MariaDBAdapter",
    "oracle": "django_tasks_aws.databases.oracle.OracleAdapter",
}


def get_database_adapter(database_alias: str) -> DatabaseAdapter:
    return get_adapter_for_connection(connections[database_alias])


def get_adapter_for_connection(connection) -> DatabaseAdapter:
    adapters = DEFAULT_ADAPTERS | getattr(settings, "TASKS_AWS_DATABASE_ADAPTERS", {})
    vendor = connection.vendor
    if (
        vendor == "mysql"
        and "mysql_server_data" in connection.__dict__
        and connection.mysql_is_mariadb
    ):
        vendor = "mariadb"
    path = adapters.get(vendor)
    if path is None:
        raise ConfigurationError("Task coordination requires PostgreSQL, MySQL, MariaDB, or Oracle")
    adapter = import_string(path)()
    if not all(hasattr(adapter, name) for name in ("clock_sql", "require_writer", "transaction")):
        raise ConfigurationError(f"Database adapter {path!r} does not implement DatabaseAdapter")
    return adapter


class DatabaseClock(Func):
    """Wall-clock time evaluated by the database, including time spent waiting for locks."""

    arity = 0
    output_field = DateTimeField()

    def as_sql(
        self, compiler, connection, function=None, template=None, arg_joiner=None, **extra_context
    ):
        return get_adapter_for_connection(connection).clock_sql, []
