"""The shared coordination transaction must be adapter-owned, not engine SQL."""

import contextlib

from django_tasks_aws.databases import context


def test_coordination_transaction_delegates_to_adapter(monkeypatch):
    delegated = {}

    class FakeAdapter:
        def transaction(self, connection):
            delegated["connection"] = connection
            return contextlib.nullcontext()

    adapter = FakeAdapter()
    connection = object()

    def fake_adapter(alias):
        delegated["alias"] = alias
        return adapter

    monkeypatch.setattr(context, "get_database_adapter", fake_adapter)
    monkeypatch.setattr(context, "connections", {"default": connection})

    with context.manage_coordination_transaction("default"):
        pass

    assert delegated == {"alias": "default", "connection": connection}
