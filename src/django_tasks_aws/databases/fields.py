"""Bounded SQL identifiers with case-sensitive, non-padding comparison."""

from django.db import models


class IdentifierField(models.CharField):
    """Keep MySQL's default case folding and space padding out of task identities."""

    def db_parameters(self, connection):
        parameters = super().db_parameters(connection)
        match connection.vendor:
            case "postgresql":
                parameters["collation"] = "C"
            case "mysql":
                parameters["collation"] = (
                    "utf8mb4_nopad_bin" if connection.mysql_is_mariadb else "utf8mb4_0900_bin"
                )
            case _:
                pass
        return parameters

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is not None and self.max_length is not None and len(value) > self.max_length:
            raise ValueError(f"{self.name} exceeds {self.max_length} characters")
        return value
