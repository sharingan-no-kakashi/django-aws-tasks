# django-tasks-aws

A serverless Django task system on AWS: a relational database as the durable owner of task
state, standard SQS queues as delivery signals, Lambda workers, database leases,
and periodic reconciliation.

The architecture is specified in [`Django tasks design.md`](Django tasks design.md).
The single [remaining-work backlog](todo-task.md) consolidates the core,
workflow, developer experience, feature, and LocalStack plans, audited against
the code on 23 October 2026. It distinguishes missing implementation from
verification still required and maps every original task ID.

Optional [DAG workflows](docs/workflows.rst) compose existing tasks with
`a >> (b & c) >> d`, persist dependencies and output references, and recover
progress through the existing sweeper. Enable `django_tasks_aws.workflows` and
apply its migrations to use the workflow layer.
Remaining reliability, recovery, and usability work is tracked in the
[consolidated backlog](todo-task.md#correctness-and-contract-gaps).

> Status: beta. The backend, coordination services, Lambda adapters, and
> local sample and Phase F operator tools are implemented. AWS deployment and
> release evidence remain incomplete. Monitoring Terraform is included. No AWS deployment is claimed.

PostgreSQL 15+ is the database covered by the integration CI (versions 15 and 16).
MySQL 8.4+, MariaDB 10.6+ with InnoDB, and Oracle 21c+ have implemented adapters
but are **experimental**: equivalent real-engine lifecycle, rollback,
deduplication, and competing-worker verification is still required. They are
not release-qualified database targets. PostgreSQL also remains subject to the
package's beta release gates. The shared
`DatabaseSqsBackend` selects an engine adapter from Django’s database connection.
See the [database guide](docs/database-backends.rst) for setup and adapter overrides.
Each package app has one initial migration for the current schema. This pre-release
supports fresh installations; earlier development schemas have no upgrade path.
The separate example and CI remain PostgreSQL-based. Run `uv sync --locked`, then
`uv run pytest` for the library suite. Run consumer tests from the example
repository with `make test`.
Database tests require the `POSTGRES_*` settings and `POSTGRES_DSN`; skipped
integration tests do not establish release readiness.

`uv run python scripts/check_traceability.py` checks the complete requirement
inventory. The release gate additionally requires passing test evidence:
`uv run python scripts/check_traceability.py --strict --junit-report results.xml`.
Generate that report with `uv run pytest --junitxml=results.xml`. Unassigned,
missing, failed, or skipped mandatory tests block the strict gate.

Operator commands, admin permissions, replay/release, retention, telemetry, and
incident procedures are documented in [operations](docs/operations.md).
[Phase F status](docs/phase-f-status.md) separates local verification from the
remaining deployed log-navigation and alert-drill evidence.

Start with the separate [`django-aws-tasks-example`](../django-aws-tasks-example/README.md) repository, the
complete consumer example with a web UI, local PostgreSQL setup, task workers,
and tests. Its [Terraform deployment](../django-aws-tasks-example/terraform/README.md)
runs the same application on AWS. Reusable infrastructure modules live in
[`terraform/modules`](terraform/modules).

Current fixes and remaining external acceptance are recorded in
[acceptance status](docs/acceptance-status.md). See the
[backend reference](docs/backend-reference.md) for configuration and execution
budgets, and the [release procedure](docs/release.md) for candidate evidence.


## Contributing and maintenance

Consumer development, local AWS emulation, browser tests, and image builds belong
to the [example repository](../django-aws-tasks-example/README.md). Run its Make
commands from that checkout.

Run `make help` to list the development commands. Start with `make setup`,
`make check`, and `make test-unit`; use `make test-postgres` for database tests
and `make docs` or `make build` for documentation and packages. Test commands
accept extra pytest options, for example `make test-unit PYTEST_ARGS="-q"`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks, and pull requests,
[SECURITY.md](SECURITY.md) for private vulnerability reporting, and the
[changelog](CHANGELOG.md) for release notes. Maintainers should complete the
[GitHub setup checklist](docs/github-maintenance.md) before relying on required
checks, release automation, or protected publishing environments.

The consumer is no longer a uv workspace member. Keep it at
`../django-aws-tasks-example` or set `TASK_EXAMPLE_ROOT` for acceptance scripts.
The library installs independently; its Make targets operate on this checkout.
