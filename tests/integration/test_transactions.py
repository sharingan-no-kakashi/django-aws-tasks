"""Real-PostgreSQL transaction and competing-connection fixtures.

These are the DT-003 evidence that the suite can observe genuine commit,
rollback, and row-lock semantics — the substrate every claim/finalization test
later builds on. SQLite is never used here.
"""

import psycopg
import pytest

from tests.support import postgres

TABLE = "dt_transaction_fixture"


@pytest.fixture
def table(postgres_connection):
    with postgres_connection.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"CREATE TABLE {TABLE} (id serial PRIMARY KEY, value int)")
    yield TABLE
    with postgres_connection.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")


@pytest.mark.postgres
def test_commit_is_visible_to_another_connection(table, postgres_connection):
    with postgres.connect() as other, other.cursor() as cur:
        cur.execute(f"INSERT INTO {table} (value) VALUES (1)")

    with postgres_connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 1


@pytest.mark.postgres
def test_rollback_is_invisible(table, postgres_connection):
    with postgres.connect() as conn:
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO {table} (value) VALUES (2)")
            conn.rollback()
        finally:
            conn.autocommit = True

    with postgres_connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0


@pytest.mark.postgres
def test_competing_connection_serializes_on_row_lock(table, postgres_connection):
    with postgres_connection.cursor() as cur:
        cur.execute(f"INSERT INTO {table} (value) VALUES (10)")

    first = postgres.connect()
    second = postgres.connect()
    first.autocommit = False
    second.autocommit = False
    try:
        with first.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} WHERE value = 10 FOR UPDATE")

        with second.cursor() as cur:
            cur.execute("SET lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                cur.execute(f"SELECT id FROM {table} WHERE value = 10 FOR UPDATE")
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
