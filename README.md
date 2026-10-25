# django-tasks-aws

A Django Tasks backend for AWS, with durable task state in SQL, standard SQS
queues for delivery, and Lambda workers for execution. Tasks can be accepted in
same-database business transactions, inspected through Django admin and operator
commands, and recovered by a scheduled sweeper.

> **Status: beta.** The backend, Lambda adapters, optional workflows, operator
> tools, and Terraform modules are implemented. AWS deployment, capacity, and
> release acceptance evidence remain incomplete; no successful cloud deployment
> or published release is claimed. See [acceptance status](docs/acceptance-status.md)
> and the [release procedure](docs/release.md).

## Design

![Task system design: Django commits task state to SQL, publishes to SQS after commit, and Lambda workers claim and finalize in SQL. EventBridge Scheduler invokes a sweeper to recover expired leases and republish due work.](docs/_static/task-system-design.svg)

The diagram uses official [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/).
The [editable SVG and icon attribution](docs/_static/task-system-design.md) live
in this repository.

1. **Accept work.** The producer inserts a task record in SQL. When business data
   and task records share a database alias and transaction, rollback removes both.
2. **Signal after commit.** An `on_commit` callback publishes a small
   `{schema_version, task_id}` envelope to the route's SQS queue. Task arguments
   and results stay in SQL.
3. **Claim and execute.** SQS invokes the route's Lambda worker. The worker checks
   the current task state and acquires a fixed, non-renewable database lease
   before running the task under a deadline. It finalizes using its lease token.
4. **Recover.** EventBridge Scheduler invokes a sweeper that repairs missed
   publications, reaps expired leases, and redispatches eligible pending work.

Each named route has its own queue and worker configuration. The Terraform task
system module also supplies dead-letter queues and the scheduled sweeper. The
application supplies its database and packages the same task code for producers
and workers.

Duplicate deliveries are checked against SQL state. Delivery retries and
execution attempts have separate budgets. A lease fences database ownership;
external actions such as payments or emails still need business idempotency.
If a worker disappears after a remote effect, policy can hold the ambiguous
outcome for audited operator review.

See the [architecture guide](docs/architecture.rst) for invariants, failure
windows, and recovery, and the [implementation specification](docs/implementation-spec.md)
for the detailed contract.

## Getting started

Requirements: **Python 3.12–3.14**, **Django 6.1**, and a supported writable SQL
database. The AWS execution path uses standard SQS queues, Lambda, and
EventBridge Scheduler. SQLite is only suitable for isolated unit tests.

For a runnable application, start with
[`django-aws-tasks-example`](https://github.com/sharingan-no-kakashi/django-aws-tasks-example).
It includes a web UI, local PostgreSQL setup, AWS emulation, workers, tests, and
[Terraform deployment](https://github.com/sharingan-no-kakashi/django-aws-tasks-example/tree/main/terraform).
Reusable infrastructure modules are in [`terraform/modules`](terraform/modules).

To integrate an existing Django application, follow the
[installation guide](docs/installation.rst) to install the database extra, add
`django_tasks_aws` to `INSTALLED_APPS`, configure `DatabaseSqsBackend` and its
worker route manifest, and apply migrations. Producer and worker artifacts must
include the same importable task modules.

Define a task at module scope in an allowed module such as `myapp/tasks.py`:

```python
from django.tasks import task

from myapp.models import Event


@task(queue_name="short")
def record_event(event_id: str) -> dict:
    updated = Event.objects.filter(pk=event_id).update(processed=True)
    return {"event_id": event_id, "found": bool(updated)}
```

Enqueue it alongside the business change, using the same database alias as the
task backend:

```python
from django.db import transaction

from myapp.models import Event
from myapp.tasks import record_event


with transaction.atomic(using="default"):
    event = Event.objects.using("default").create(kind="signup")
    result = record_event.enqueue(event_id=str(event.pk))
```

The task becomes durable when the outer transaction commits. Refresh the returned
snapshot to read its current state:

```python
from django.tasks import TaskResultStatus

result.refresh()
if result.status == TaskResultStatus.SUCCESSFUL:
    print(result.return_value)
```

The [quick start](docs/quickstart.rst) covers local execution, result lookup, and
keyed submission deduplication. See [configuration](docs/configuration.rst) for
routes, allowed modules, timing budgets, and retry policies.

## Optional workflows

[DAG workflows](docs/workflows.rst) compose tasks with `a >> (b & c) >> d`.
The workflow layer persists dependencies and output references, and recovers
progress through the same sweeper and task execution services. Add
`django_tasks_aws.workflows` to `INSTALLED_APPS` and apply its migrations to use it.

## Database support

| Database | Current support |
| --- | --- |
| PostgreSQL 15+ | Full integration suite; on-demand CI covers versions 15 and 16. Release qualification target, still subject to beta gates. |
| MySQL 8.4+ / MariaDB 10.6+ | Implemented, experimental adapters; InnoDB required. Equivalent real-engine lifecycle, rollback, deduplication, and competing-worker evidence remains required. |
| Oracle 21c+ | Implemented, experimental adapter; equivalent real-engine verification remains required. |

`DatabaseSqsBackend` selects an adapter from Django's database connection.
Experimental adapters are not release-qualified targets. See the
[database guide](docs/database-backends.rst) for setup and adapter overrides.
Each package app has one initial migration for the current schema. This
pre-release supports fresh installations; earlier development schemas have no
upgrade path.

## Operations and documentation

- [Operations](docs/operations.md): inspection commands, admin permissions,
  holds, audited replay/release, retention, telemetry, and incident procedures.
- [Backend reference](docs/backend-reference.md): configuration and execution budgets.
- [Phase F status](docs/phase-f-status.md): local operator verification and
  outstanding deployed log-navigation and alert-drill evidence.
- [Release procedure](docs/release.md): candidate evidence and publishing gates.

## Development and verification

From this library checkout:

```console
uv sync --locked --all-groups
make check
make test
```

`make test` (or `uv run pytest`) runs fast unit tests without external services.
Use `make test-postgres` for the full database integration suite, or
`make test-e2e` to also run the separate consumer and browser journeys. Database
tests require the `POSTGRES_*` settings and `POSTGRES_DSN`; skipped integration
tests do not establish release readiness.

PR CI runs unit tests. The **PostgreSQL end-to-end tests** and **Database
compatibility matrix** workflows run on demand from GitHub Actions. The matrix
covers Python 3.12–3.14 with PostgreSQL 15/16, MySQL 8.4, MariaDB 10.6, and Oracle
23 using shared real-engine contracts. See [test setup](CONTRIBUTING.md#tests-and-checks)
for local commands and the distinction between experimental and qualified engines.

`make check` includes the requirement inventory check. The strict release gate
also needs passing test evidence:

```console
REQUIRE_POSTGRES=1 uv run pytest tests/unit tests/integration --junitxml=results.xml
uv run python scripts/check_traceability.py --strict --junit-report results.xml
```

Unassigned, missing, failed, or skipped mandatory tests block the strict gate.

Run `make help` for all development commands, `make docs` to build the Sphinx
documentation, and `make build` for distributions. Test commands accept extra
pytest options, for example `make test-unit PYTEST_ARGS="-q"`.

Consumer development, local AWS emulation, browser tests, and image builds belong
to the example repository. Run its Make commands from that checkout. The consumer
is a separate project, not a uv workspace member. Keep it at
`../django-aws-tasks-example` for `make test-e2e`; acceptance scripts also accept
`TASK_EXAMPLE_ROOT`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull requests,
[private vulnerability reporting](https://github.com/sharingan-no-kakashi/django-aws-tasks/security/advisories/new),
and [GitHub releases](https://github.com/sharingan-no-kakashi/django-aws-tasks/releases)
for release notes. Maintainers should complete the
[GitHub setup checklist](docs/github-maintenance.md) before relying on required
checks, release automation, or protected publishing environments.
