"""Initial migrations install the complete current schema without an upgrade chain."""

import subprocess
import sys

import pytest

SCHEMA_PROBE = """
import sys
from django.conf import settings
app_label = sys.argv[1]
installed = ["django_tasks_aws"]
if app_label == "django_tasks_workflows":
    installed.append("django_tasks_aws.workflows")
settings.configure(INSTALLED_APPS=installed, SECRET_KEY="migration-test",
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
import django
django.setup()
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
call_command("migrate", verbosity=0)
loader = MigrationLoader(connection)
migrations = [key for key in loader.disk_migrations if key[0] == app_label]
assert migrations == [(app_label, "0001_initial")]
migration = loader.disk_migrations[migrations[0]]
assert migration.initial and not migration.replaces
state = loader.project_state().apps
with connection.cursor() as cursor:
    for model in apps.get_app_config(app_label).get_models():
        migrated = state.get_model(app_label, model.__name__)
        assert {field.name for field in migrated._meta.fields} == {
            field.name for field in model._meta.fields
        }
        columns = connection.introspection.get_table_description(cursor, model._meta.db_table)
        assert {field.column for field in model._meta.fields} == {column.name for column in columns}
        constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
        expected = model._meta.constraints + model._meta.indexes
        assert all(item.name in constraints for item in expected)
"""


@pytest.mark.parametrize("app_label", ["django_tasks_aws", "django_tasks_workflows"])
def test_initial_migrations_create_current_schema(app_label):
    subprocess.run([sys.executable, "-c", SCHEMA_PROBE, app_label], check=True)
