"""Pytest configuration for the library suite.

Detects whether a real PostgreSQL instance is reachable and skips tests marked
``postgres`` when it is not, so an unavailable optional local service is clearly
distinguishable from a skipped release requirement (which CI must not skip).
"""

import os

import pytest

from tests.support import postgres


def make_manifest() -> dict:
    """A valid two-worker route manifest for backend construction."""
    base = {
        "source_queue_arn": "arn:aws:sqs:us-east-1:123:queue",
        "source_queue_url": "https://sqs.us-east-1.amazonaws.com/123/queue",
        "timeout_seconds": 60,
        "lease_seconds": 90,
        "visibility_timeout_seconds": 360,
        "max_receive_count": 5,
        "concurrency": 10,
        "dispatch_limit": 12,
        "redispatch_seconds": 300,
    }
    return {
        name: dict(
            base,
            source_queue_arn=f"arn:aws:sqs:us-east-1:123:{name}",
            source_queue_url=f"https://sqs.us-east-1.amazonaws.com/123/{name}",
        )
        for name in ("short", "documents")
    }


@pytest.fixture
def test_backend():
    """A DatabaseSqsBackend bound to the real ``postgres`` alias."""
    from django_tasks_aws.backends import DatabaseSqsBackend

    return DatabaseSqsBackend(
        "default",
        {
            "QUEUES": ["short", "documents"],
            "OPTIONS": {
                "database_alias": "postgres",
                "workers": make_manifest(),
                "allowed_task_modules": ["tests.demo_tasks"],
            },
        },
    )


@pytest.fixture
def manifest():
    from django_tasks_aws.schemas import load_route_manifest

    return load_route_manifest(make_manifest())


@pytest.fixture
def fake_transport():
    from tests.support.fake_sqs import FakeSqsTransport

    return FakeSqsTransport()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "postgres: requires a real PostgreSQL instance")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if not any("postgres" in item.keywords for item in items):
        return
    if postgres.reachable():
        return
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        raise pytest.UsageError("Mandatory PostgreSQL integration service is unavailable")
    skip = pytest.mark.skip(reason="PostgreSQL is not reachable; start it or set POSTGRES_DSN")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def postgres_connection():
    """An independent, autocommitting connection to real PostgreSQL."""
    if not postgres.reachable():
        pytest.skip("PostgreSQL is not reachable")
    with postgres.connect() as conn:
        yield conn
