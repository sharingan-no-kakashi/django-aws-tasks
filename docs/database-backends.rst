=================
Database backends
=================

Use ``django_tasks_aws.backends.DatabaseSqsBackend`` with PostgreSQL 15+,
MySQL 8.4+, MariaDB 10.6+, or Oracle 21c+. The connection selected by ``OPTIONS['database_alias']`` determines
the adapter. MySQL, MariaDB, and Oracle adapters are **experimental**, not
release-qualified targets. The integration CI currently covers PostgreSQL 15
and 16 only; an implemented adapter does not establish verified engine support.
The entire package remains beta.

Before promoting an experimental engine, run the shared lifecycle, rollback,
submission deduplication, lease fencing, competing-worker, and workflow recovery
contracts on that real engine, including fresh migrations and connection-limit
restoration after failure. Mocked SQL checks do not replace this evidence.

Producers, workers, and the sweeper must use the same writable
primary and schema. SQLite is not a coordination backend.

Install and configure
=====================

Install the driver for your engine:

.. code-block:: console

   uv add 'django-tasks-aws[postgres]'
   # or:
   uv add 'django-tasks-aws[mysql]'
   uv add 'django-tasks-aws[mariadb]'
   uv add 'django-tasks-aws[oracle]'

The MySQL extra uses ``mysqlclient``; builds from source require MySQL client
headers and ``pkg-config``. The driver extras can be installed together.

A MySQL connection can be configured as follows. Supply credentials through your
normal environment or secrets configuration:

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django.db.backends.mysql",
           "NAME": "application",
           "HOST": DATABASE_HOST,
           "USER": DATABASE_USER,
           "PASSWORD": DATABASE_PASSWORD,
           "OPTIONS": {
               "charset": "utf8mb4",
               "init_command": "SET time_zone = '+00:00'",
               "isolation_level": "read committed",
           },
       },
   }

Keep Django and the MySQL session in UTC (use ``USE_TZ = True`` and leave the
connection ``TIME_ZONE`` unset). Use InnoDB, strict SQL mode (``STRICT_TRANS_TABLES`` or ``STRICT_ALL_TABLES``),
and leave ``sysdate-is-now`` disabled. Invocation startup checks the server
version, writable state, isolation, default storage engine, task-table engines,
strict mode, UTC time zones, replication format, and wall-clock behavior. Apply all migrations before starting work.
A PostgreSQL connection continues to use ``django.db.backends.postgresql``.
The bundled sample and its Terraform deployment still provision PostgreSQL.

Transactions and time
=====================

All task transitions share the same ORM services: row locks, conditional updates,
lease fencing, and consistent task-before-attempt lock ordering. Only engine
operations live behind :class:`django_tasks_aws.databases.base.DatabaseAdapter`.

.. list-table::
   :header-rows: 1

   * - Operation
     - PostgreSQL
     - MySQL
   * - Wall clock
     - ``CLOCK_TIMESTAMP()``
     - ``SYSDATE(6)``
   * - Lock wait limit
     - Transaction-local 1 second
     - Session InnoDB/metadata limits of 1 second
   * - Query execution limit
     - Transaction-local statement timeout of 2 seconds
     - Session ``max_execution_time`` of 2000 milliseconds for eligible SELECTs
   * - Cleanup
     - Restore caller limits; rollback restores transaction-local settings
     - Restore session limits on success or rollback; discard unusable connections

MySQL's execution limit does not bound writes or every locking statement. It is
not equivalent to PostgreSQL's statement timeout. Configure driver/network
budgets and retain invocation deadlines; see :doc:`configuration`. Session
limits are restored before successful commit callbacks execute, including when
coordination runs inside an application's transaction. MySQL must use row-based
binary logging when replication is enabled because ``SYSDATE()`` is unsafe for
statement-based replication.

Identifiers and upgrades
========================

Identifiers compare exactly, including case and trailing spaces: PostgreSQL uses
``C`` collation and MySQL uses ``utf8mb4_0900_bin`` (NO PAD). Submission uniqueness
uses an ordinary unique constraint on ``(dedupe_scope, dedupe_key)``. Multiple
NULL keys are allowed; two equal non-NULL keys in the same scope conflict.
This works on both engines without conditional unique indexes.

Indexed identifiers have explicit character limits:

.. list-table::
   :header-rows: 1

   * - Identifier
     - Maximum characters
   * - Task import path
     - 512
   * - Backend alias, deduplication scope/key, operation key, control target scope
     - 255
   * - Queue and worker name
     - 80
   * - Control request ID
     - 200

Oversized values raise an error during ORM preparation. Each app's
``0001_initial`` migration creates the current bounded identifiers, constraints,
and portable indexes directly. This pre-release supports fresh schemas only;
recreate disposable development databases made with earlier migration chains.
MySQL schema changes are not transactional; inspect a failed installation before
retrying. Portable composite indexes cover dispatch, recovery, and active attempts.

Swapping engine operations
==========================

A custom adapter implements ``clock_sql``, ``require_writer(connection)``, and
``transaction(connection)``. The transaction method returns a context manager
that owns the atomic block and restores connection settings. The clock must
advance during lock waits, rather than remaining fixed at transaction start.
``require_writer`` must reject engines or connection settings that cannot uphold
the coordination guarantees.

Override a built-in adapter, or register another Django connection vendor,
through settings:

.. code-block:: python

   TASKS_AWS_DATABASE_ADAPTERS = {
       "mysql": "myapp.database.CustomMySQLAdapter",
   }

Subclass ``MySQLAdapter`` or ``PostgresAdapter`` when changing one operation.
The resolver reads settings without opening a connection, so Django startup
checks can validate configuration offline. MySQL-family flavor detection is
deferred until Django has server information. Runtime SQL expressions resolve the
adapter from the connection actually compiling the query. Schema portability
is separate: new engines must support the identifier fields, constraints,
transactions, and row-lock options before they can be considered supported.

The SQS transport remains a separate component through ``SqsTransport``;
changing database adapters does not change delivery behavior or task policies.

MariaDB
=======

MariaDB 10.6+ uses Django's ``django.db.backends.mysql`` engine and the
``django-tasks-aws[mariadb]`` extra. Use the same UTC, strict-mode, InnoDB,
READ COMMITTED, and row-based replication settings shown for MySQL above.
The server flavor is discovered when the connection is opened; configuration
checks do not open a connection just to distinguish MySQL from MariaDB.

MariaDB uses ``utf8mb4_nopad_bin`` for identifiers and scopes its own
``max_statement_time`` to two seconds, restoring the caller's value afterwards.
It also uses its supported transaction-state variables and omits the unsupported
``FOR UPDATE OF`` option. The already-held parent task lock preserves lock
ordering when an attempt query also joins the task table.

Build ``mysqlclient`` against a compatible client library. For MariaDB,
MariaDB Connector/C is suitable. Some recent MySQL client libraries remove
``mysql_native_password`` support even though MariaDB servers still use it;
that is a client authentication incompatibility, independent of the adapter.

Oracle
======

Oracle 21c+ uses Django's ``django.db.backends.oracle`` engine and the
``django-tasks-aws[oracle]`` extra. Oracle 19c is outside this task backend's
support boundary because task results may contain scalar JSON values.
The default python-oracledb Thin mode does not require Oracle Instant Client.

.. code-block:: python

   DATABASES = {
       "default": {
           "ENGINE": "django.db.backends.oracle",
           "NAME": "database-host:1521/application-service",
           "USER": DATABASE_USER,
           "PASSWORD": DATABASE_PASSWORD,
       },
   }

Use ``USE_TZ = True``, the default UTC connection time zone, READ COMMITTED
isolation, and binary identifier comparisons. Keep ``NLS_COMP`` and ``NLS_SORT``
set to ``BINARY`` and use ``BINARY`` or ``USING_NLS_COMP`` column collations.
Startup rejects other comparison settings, standby roles, and read-only
connections. The write check performs a zero-row update on the task table,
requiring ordinary task-table UPDATE permission rather than privileged access
to ``V$DATABASE``.

Oracle's adapter uses ``SYS_EXTRACT_UTC(SYSTIMESTAMP)`` as a wall clock and
scopes the driver's ``call_timeout`` to at most 2000 milliseconds. This bounds
each database round trip, including lock waits, rather than the complete
transaction. The previous deadline is restored before commit callbacks and
after rollback. Invocation deadlines still provide the outer budget.

Django's Oracle backend cannot combine its SQL row-limit syntax with
``FOR UPDATE``. Single-identity locks use ``get()`` without a limit. Batches
use a ``SKIP LOCKED`` cursor with prefetch disabled and fetch only the requested
number of identities. This avoids locking an extra prefetched batch.

Oracle also treats empty strings as NULL and normally considers two composite
keys with the same scope and a NULL submission key duplicates. The initial
migrations use a function-based unique index whose scope becomes NULL when the key is
NULL. Unkeyed tasks can therefore coexist while keyed duplicates remain rejected
by the database. Public submission keys and operation keys must be non-empty.
The identifier limits above apply. PostgreSQL and MySQL use their ordinary
unique constraint under the same submission-key abstraction.

Adapter overrides
=================

The ``mysql`` registry key selects the initial MySQL-family dispatcher. Once
Django discovers MariaDB, the ``mariadb`` registry key selects its adapter.
Use ``oracle`` for an Oracle override. Database adapter lifecycle operations,
portable row-lock helpers, and submission constraints are independent components;
task execution, policies, and SQS delivery stay shared.

Why the writable primary matters
================================

A claim must observe current ownership and prevent a competing claim in the same
transaction. A replica can be behind the writer even when a task appears present,
and a read-only connection cannot persist the lease. Route all coordination and
result-sensitive task operations to the configured writable primary. Database
routers and failover configurations must preserve that choice.

Database time is also part of ownership. A transaction-start timestamp can remain
unchanged while a query waits for a lock; checking expiry against that value
could let an already-expired lease finalize. The adapter's wall clock advances
during the wait so finalization checks the time ownership is actually tested.

Keep network calls and business execution outside coordination transactions.
Task bodies can create their own short application transactions, but holding SQL
locks while waiting for a remote API increases contention and does not make the
remote effect transactional.

Validate an engine deployment
=============================

For a fresh deployment, verify the following on the actual engine and driver:

#. Apply both core and optional workflow migrations to the selected database.
#. Confirm startup writer checks accept its version, isolation, time zone, and
   comparison settings.
#. Enqueue and execute a task, then verify result retrieval and attempt history.
#. Roll back a transaction that creates business data and a task; neither should
   remain, and no commit publication should occur.
#. Exercise competing claims and expired-lease recovery, including a stale
   finalization that must not overwrite the new owner.
#. Check that connection limits are restored after success and rollback, including
   reused connections and application-owned outer transactions.

The shared repository integration contracts provide the starting point for
these checks. Successful SQL generation or an isolated driver connection proves
less than concurrent lifecycle behavior. Preserve this distinction when reporting
experimental engine results.

Connection capacity
===================

Persistent connections improve warm invocation latency, but each execution
environment can retain its own connection. Budget database capacity for the
combined routes and additional producers, sweepers, and maintenance processes.
Watch connection count, lock waits, and query duration alongside queue depth.
A database proxy may help connection management, but it must preserve the session
and transaction behavior required by the selected adapter.
