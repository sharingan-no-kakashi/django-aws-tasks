"""Submission uniqueness with equivalent NULL semantics on every supported engine."""

from django.db import models


class SubmissionKeyConstraint(models.UniqueConstraint):
    def oracle_constraint(self):
        scope, key = self.fields
        indexed_scope = models.Case(
            models.When(**{f"{key}__isnull": False}, then=models.F(scope)),
            default=models.Value(None),
            output_field=models.CharField(max_length=255),
        )
        return models.UniqueConstraint(indexed_scope, models.F(key), name=self.name)

    def constraint_sql(self, model, schema_editor):
        if schema_editor.connection.vendor == "oracle":
            return self.oracle_constraint().constraint_sql(model, schema_editor)
        return super().constraint_sql(model, schema_editor)

    def create_sql(self, model, schema_editor):
        if schema_editor.connection.vendor == "oracle":
            return self.oracle_constraint().create_sql(model, schema_editor)
        return super().create_sql(model, schema_editor)

    def remove_sql(self, model, schema_editor):
        if schema_editor.connection.vendor == "oracle":
            return self.oracle_constraint().remove_sql(model, schema_editor)
        return super().remove_sql(model, schema_editor)
