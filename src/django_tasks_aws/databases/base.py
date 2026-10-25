"""The database-specific operations required by the shared coordination services."""

from contextlib import AbstractContextManager
from typing import Protocol

from django.db.backends.base.base import BaseDatabaseWrapper


class DatabaseAdapter(Protocol):
    """Implement this contract to plug another SQL engine into coordination."""

    clock_sql: str

    def require_writer(self, connection: BaseDatabaseWrapper) -> None: ...

    def transaction(self, connection: BaseDatabaseWrapper) -> AbstractContextManager[None]: ...
