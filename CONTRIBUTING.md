# Contributing

This is a personal prototype associated with a blog post. Do not use it as a
production system. Read the [architecture](docs/architecture.rst) before changing
delivery, transaction, lease, retry, or recovery behavior.

## Development setup

Use Python 3.12–3.14 and [uv](https://docs.astral.sh/uv/). Fork the repository,
branch from `main`, and install the locked dependencies:

```sh
git clone https://github.com/YOUR-USERNAME/django-aws-tasks.git
cd django-aws-tasks
uv sync --locked --all-groups
make hooks
make check
make test-unit
```

The [example application guide](docs/example-repository.md)
explains the companion checkout and its local tooling. Its README covers
dependencies, the local database, web UI, and AWS emulation. For library
end-to-end checks, place its checkout at `../django-aws-tasks-example`. Other
acceptance scripts support `TASK_EXAMPLE_ROOT`.

Never commit credentials, real task payloads, Terraform state, or private logs.

## Tests and checks

The [testing guide](docs/testing.rst) is the reference for suite commands,
services, CI workflows, and recorded results. `make test` defaults to unit tests.
`make test-postgres` runs the database integration suite; `make test-e2e` also
runs the example and browser journeys. Use disposable databases.

A local PostgreSQL configuration can use:

```sh
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export POSTGRES_DB=django_tasks_aws_test
export POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/django_tasks_aws_test
make test-postgres
```

These credentials are for a disposable local instance only. The database user
must be allowed to create test databases. See [database setup](docs/database-backends.rst)
for other engines and driver requirements.

`make check` checks formatting, lint, typing, requirement assignments, and local
documentation links. `make docs` builds Sphinx with warnings as errors;
`make docs-linkcheck` checks external links in the Sphinx guides. Update public
guides alongside behavior changes. Historical reviews and plans belong in
`docs/archive/`; track actionable work in
[GitHub Issues](https://github.com/sharingan-no-kakashi/django-aws-tasks/issues).

The pre-push hooks run `make check`, `make test-unit`, and `make audit`.
The audit requires access to vulnerability data. Run the hooks manually with:

```sh
uv run --locked pre-commit run --hook-stage pre-push --all-files
```

Terraform changes need formatting, validation, and mock-provider tests. Update
`docs/verification-matrix.yaml` when changing a requirement's test assignment.
Use `make format` for Python formatting and lint fixes.

## Code conventions

Name functions with a verb and domain concept: `get_task_result`,
`build_control_summary`, `validate_identifier`, or `report_progress`.
Use `is_` and `has_` for predicates; properties keep noun names. Preserve
framework hooks, protocol names, and the public workflow DSL.

Use decorators for function-wide transactions, telemetry, and execution scopes.
`scoped` resolves context arguments from call parameters, including dotted paths:

```python
from django_tasks_aws.decorators import scoped
from django_tasks_aws.databases.context import manage_coordination_transaction


@scoped(manage_coordination_transaction, "database_alias")
def update_records(database_alias, records):
    return records.update(...)
```

Extract a focused helper when only part of an operation belongs inside a scope.
Keep validation, post-commit reporting, and business execution outside
coordination transactions. Stacked decorators enter from top to bottom and exit
in reverse order. Use `with` for low-level resources and context-manager
implementations; `scoped` accepts only synchronous functions that do not yield.

Update imports, string-based patches, public exports, examples, and docs together
when renaming helpers. Do not rename persisted fields or event names casually.

When changing dependencies, update `pyproject.toml` and `uv.lock` together with
`uv lock`. Prefer targeted upgrades such as `uv lock --upgrade-package boto3`.
The example owns its separate lock and exported image requirements.

## Pull requests

Open an issue before substantial API or architecture changes. Small fixes may
go straight to a PR. Describe the concrete problem, resulting behavior,
validation, and any migration or rollback impact. Link issues with `Fixes #123`.

Use Conventional Commit messages and PR titles, for example:

- `fix: prevent redispatch of a completed task`
- `feat: add a worker health check`
- `docs: explain task retention`
- `feat!: change worker routing configuration`

Supported types are `feat`, `fix`, `perf`, `docs`, `refactor`, `test`, `build`,
`ci`, `chore`, `revert`, `style`, and `deps`; scopes are optional. Breaking
changes need migration instructions and a `BREAKING CHANGE:` explanation.
Maintainers squash merge using the validated PR title. Required checks and
maintainer review must pass.

## Releases and security

Release Please prepares version and changelog PRs. Maintainers use the
[release procedure](docs/release.md) for verification and publication, and the
[GitHub setup guide](docs/github-maintenance.md) for repository configuration.

Report vulnerabilities privately through the [security policy](SECURITY.md).
Follow the [code of conduct](CODE_OF_CONDUCT.md).
