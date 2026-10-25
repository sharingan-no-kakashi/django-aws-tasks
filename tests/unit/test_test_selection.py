"""Fast test selection never depends on an external database."""

from unittest.mock import Mock

import pytest

from tests import conftest


def test_unit_collection_does_not_probe_postgres(monkeypatch):
    probe = Mock(side_effect=AssertionError("Unit tests must not probe PostgreSQL"))
    monkeypatch.setattr(conftest.postgres, "reachable", probe)
    monkeypatch.setenv("REQUIRE_POSTGRES", "1")
    conftest.pytest_collection_modifyitems(Mock(), [Mock(keywords={})])
    probe.assert_not_called()


def test_required_integration_database_cannot_be_skipped(monkeypatch):
    monkeypatch.setattr(conftest.postgres, "reachable", lambda: False)
    monkeypatch.setenv("REQUIRE_POSTGRES", "1")
    with pytest.raises(pytest.UsageError, match="Mandatory PostgreSQL"):
        conftest.pytest_collection_modifyitems(Mock(), [Mock(keywords={"postgres": True})])


def test_optional_database_skip_only_affects_postgres_tests(monkeypatch):
    monkeypatch.setattr(conftest.postgres, "reachable", lambda: False)
    monkeypatch.delenv("REQUIRE_POSTGRES", raising=False)
    unit = Mock(keywords={})
    integration = Mock(keywords={"postgres": True})
    conftest.pytest_collection_modifyitems(Mock(), [unit, integration])
    unit.add_marker.assert_not_called()
    integration.add_marker.assert_called_once()
