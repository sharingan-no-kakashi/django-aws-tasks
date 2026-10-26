=======
Testing
=======

The test suites cover task acceptance, serialization, transaction rollback,
claims, lease fencing, retry and recovery, operator controls, retention, and
optional workflow transitions. Database tests use disposable server databases;
unit tests use mocks and small in-memory SQLite schema fixtures.

Run the checks
==============

From the library checkout:

.. code-block:: console

   uv sync --locked --all-groups
   make check
   make test-unit
   make docs
   make docs-links

``make check`` runs formatting, lint, typing, requirement-assignment checks, and
local documentation-link validation. ``make docs`` builds Sphinx with warnings
as errors. ``make docs-links`` checks repository links, including archived files,
without requiring network access. ``make docs-linkcheck`` also checks external
links in the Sphinx guides.

Database and consumer suites
============================

Use disposable databases and credentials. Set ``POSTGRES_HOST``,
``POSTGRES_PORT``, ``POSTGRES_USER``, ``POSTGRES_PASSWORD``, ``POSTGRES_DB``, and
``POSTGRES_DSN`` consistently, then run:

.. code-block:: console

   make test-postgres
   make test-e2e

The end-to-end target also needs the example repository at
``../django-aws-tasks-example``, its locked dependencies, and Chromium installed
through its browser dependency group. The PostgreSQL target requires a reachable
service and fails if one is unavailable.

Shared database contracts use ``TEST_DB_NAME``, ``TEST_DB_USER``,
``TEST_DB_PASSWORD``, ``TEST_DB_HOST``, and ``TEST_DB_PORT``:

.. code-block:: console

   make test-compatibility TEST_DATABASE=postgres

Select ``mysql``, ``mariadb``, or ``oracle`` for the other adapters. See
:doc:`database-backends` for server and driver settings.

GitHub Actions runs unit checks on pull requests. The on-demand PostgreSQL
end-to-end, database compatibility, and installed-artifact workflows cover their
respective suites. The compatibility workflow selects Python 3.12–3.14 with
PostgreSQL 15/16, MySQL 8.4, MariaDB 10.6, and Oracle 23. Infrastructure jobs run
Terraform formatting, validation, mock-provider tests, and runtime-manifest/IAM
checks.

Recorded verification
=====================

The 23 October 2026 verification record reports these results on CPython
3.12.13, Django 6.1.2, and PostgreSQL 16.15:

.. list-table::
   :header-rows: 1

   * - Check
     - Result
   * - Library suite
     - 387 passed, no skips
   * - Example non-browser suite
     - 39 passed
   * - Installed wheel with minimum runtime dependencies
     - 249 passed, no skips
   * - Installed source distribution with resolved latest dependencies
     - 249 passed, no skips
   * - Headless imports, with workflows enabled and disabled
     - Passed
   * - Formatting, lint, requirement inventory, and typing
     - Passed
   * - Sphinx build with warnings as errors
     - Passed
   * - Mock-provider Terraform module tests
     - 8 passed

The same day's earlier review also records a passing Chromium operator recovery
journey. These dated observations describe their recorded environments and
revisions. Current runs produce their own results; version boundaries are listed
in :doc:`installation` and engine status in :doc:`database-backends`.

Installed distributions
=======================

Build wheel and source distributions, then verify them outside the source tree:

.. code-block:: console

   make build
   uv run python scripts/verify_installed.py --dist dist --artifact wheel --dependency-bounds minimum --report artifacts/installed-wheel.xml
   uv run python scripts/verify_installed.py --dist dist --artifact sdist --report artifacts/installed-sdist.xml

Keep JUnit reports with the tested revision and artifact hashes. See the
repository's ``docs/release.md`` for maintainer verification and publication
procedures.
