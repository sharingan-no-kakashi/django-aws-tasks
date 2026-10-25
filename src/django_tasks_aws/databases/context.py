"""Database transaction and invocation connection contexts.

Django's request lifecycle hooks do not surround an arbitrary Lambda
invocation, so connection cleanup must be explicit. Warm environments and
failed invocations must not leak connections or leave open coordination
transactions.
"""

from collections.abc import Generator
from contextlib import contextmanager

from django.db import close_old_connections, connections

from django_tasks_aws.databases import get_database_adapter


@contextmanager
def manage_coordination_transaction(database_alias: str) -> Generator[None]:
    """Keep coordination waits shorter than the invocation's safety margin."""
    with get_database_adapter(database_alias).transaction(connections[database_alias]):
        yield


@contextmanager
def open_coordination_cursor(database_alias: str):
    with (
        manage_coordination_transaction(database_alias),
        connections[database_alias].cursor() as cursor,
    ):
        yield cursor


@contextmanager
def manage_invocation_connections() -> Generator[None]:
    """Recycle obsolete or broken connections, retaining healthy persistent ones."""
    close_old_connections()
    try:
        yield
    finally:
        close_old_connections()


def close_all_connections() -> None:
    connections.close_all()
