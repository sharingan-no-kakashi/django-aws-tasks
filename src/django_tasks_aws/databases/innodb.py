"""Dispatch MySQL-family operations after Django discovers the server flavor."""

from django.conf import settings
from django.utils.module_loading import import_string

from django_tasks_aws.databases.mariadb import MariaDBAdapter
from django_tasks_aws.databases.mysql import MySQLAdapter


class InnoDBAdapter:
    clock_sql = "SYSDATE(6)"

    def server_adapter(self, connection):
        if connection.mysql_is_mariadb:
            overrides = getattr(settings, "TASKS_AWS_DATABASE_ADAPTERS", {})
            path = overrides.get("mariadb")
            return import_string(path)() if path else MariaDBAdapter()
        return MySQLAdapter()

    def require_writer(self, connection):
        self.server_adapter(connection).require_writer(connection)

    def transaction(self, connection):
        return self.server_adapter(connection).transaction(connection)
