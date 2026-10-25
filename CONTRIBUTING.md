# Contributing

Thanks for contributing to django-tasks-aws. This is a beta task backend:
changes to delivery, leases, retries, transactions, and recovery need particular
care. Read the [architecture](docs/architecture.rst) and
[execution contract](docs/release.rst) before changing those behaviors.

## Development setup

Use Python 3.12–3.14, [uv](https://docs.astral.sh/uv/), and PostgreSQL 15 or newer.
CI exercises PostgreSQL 15 and 16. Fork the repository, create a branch from
`main`, and install the locked dependencies:

```sh
git clone https://github.com/YOUR-USERNAME/django-aws-tasks.git
cd django-aws-tasks
uv sync --locked --all-groups
make hooks
make check
uv run pytest tests/unit
```

The sample is a standalone repository with its own dependencies and lock. See its
[README](../django-aws-tasks-example/README.md) for the local database and web UI.
Never commit credentials, real task payloads, Terraform state, or private logs.

## Tests and checks

For the full library suite, start a disposable PostgreSQL instance
with a user allowed to create test databases. The following credentials are for
local development only; the database must be disposable:

```sh
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export POSTGRES_DB=django_tasks_aws_test
export POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/django_tasks_aws_test
export REQUIRE_POSTGRES=1
make test
make docs
make build
uv run python scripts/check_traceability.py
```

Without `REQUIRE_POSTGRES=1`, unavailable PostgreSQL tests may be skipped. That
is useful for local work, but is not full integration or release evidence.
Run consumer and browser checks from `../django-aws-tasks-example` using
`make test` and `make test-browser`. That checkout also owns local AWS emulation,
database containers, and Lambda image builds.
Terraform changes also need formatting, validation, and the mock-provider tests
run by the `infrastructure` and `monitoring` CI jobs.

Use `make format` to format Python and apply lint fixes. `make check` includes
`scripts/`, the library, tests, and Sphinx configuration. Keep changes focused; include regression
coverage for behavior changes and explain the failure that the check catches.

Use decorators for function-wide transactions, telemetry, and execution scopes.
`scoped` resolves its context arguments from each call's parameters, including
dotted attribute paths:

```python
from django_tasks_aws.decorators import scoped
from django_tasks_aws.services.lifecycle import coordination_transaction


@scoped(coordination_transaction, "database_alias")
def update_records(database_alias, records):
    return records.update(...)
```

Extract a focused decorated helper when only part of an operation belongs inside
the scope. Keep validation, post-commit reporting, and business execution outside
transactions where required. Stacked decorators enter from top to bottom and
exit in reverse order. Use `with` for low-level resources such as file handles
and cursors, and inside context-manager implementations; `scoped` accepts only
synchronous functions that do not yield.

`make audit` checks all locked library and development dependencies with
pip-audit. The required CI security job runs the same check; review findings and
update affected dependencies rather than silently suppressing advisories.

The library suite includes a Hypothesis state machine that generates lifecycle
sequences against real PostgreSQL and shrinks failing examples. Run it with
`uv run pytest tests/integration/test_hypothesis_lifecycle.py`; the existing
seeded and process-race tests continue to cover their separate scenarios.
Hypothesis caches failures in the ignored `.hypothesis/` directory for replay.
SQS and sample Lambda adapter tests use Botocore Stubber without real AWS
credentials or network calls; the fake transport separately exercises accepted
messages with lost responses.
Update the [verification matrix](docs/verification-matrix.yaml) when a change
affects an assigned requirement. Update user and operator docs with behavior,
configuration, migration, and compatibility changes.

When changing library dependencies, update `pyproject.toml` and `uv.lock` together:

```sh
uv lock
```

Use targeted upgrades (for example `uv lock --upgrade-package boto3`) when
possible. If the consumer's dependencies also change, refresh its own lock and
exported image requirements in that repository with `uv lock` and
`make export-lock`.

## Pull requests

Open an issue before substantial API or architecture changes. Bug fixes and
small documentation corrections can go straight to a PR. Describe the concrete
problem, resulting behavior, validation, and any deployment or rollback impact.
Use the PR checklist and link the issue with `Fixes #123` when appropriate.

Use Conventional Commit messages and PR titles so commits can drive release notes.
The local `commit-msg` hook rejects messages that do not follow
`type(scope): description`, where the scope is optional. `make setup` installs
dependencies and the hook; after an existing checkout is synchronized, run
`make hooks` to install it. Each new clone needs its own hook installation.
Examples:

- `fix: prevent redispatch of a completed task`
- `feat: add a worker health check`
- `docs: explain task retention`
- `ci: validate documentation on pull requests`
- `feat!: change worker routing configuration` for a breaking change, with
  migration instructions and a `BREAKING CHANGE:` explanation in the body.

Supported types are `feat`, `fix`, `perf`, `docs`, `refactor`, `test`, `build`,
`ci`, `chore`, `revert`, `style`, and `deps`; an optional scope is welcome.
Maintainers squash merge using the validated PR title. All required checks must
pass and a maintainer must review the change. Untrusted PRs never receive AWS
credentials or access to publishing environments.

## Releases and security

Release Please prepares release PRs; maintainers follow the
[release procedure](docs/release.md) to verify and publish the exact candidate.
Merging a release PR alone does not publish a package or create a release tag.

Report vulnerabilities privately using the [security policy](SECURITY.md), not
in a public issue. Follow the [code of conduct](CODE_OF_CONDUCT.md), discuss
technical decisions with evidence, and help keep reviews useful and welcoming.

## Function naming

Use a verb followed by the domain concept for functions and methods:
`get_task_result`, `build_control_summary`, `validate_identifier`, and
`report_progress`. Use `get_` for reading values, `build_` for constructing
values, `compute_` for calculations, and `is_` or `has_` for boolean predicates.
Use `manage_`, `bind_`, or `open_` to describe context managers' effects.
Properties keep noun names. Preserve framework hooks, protocol-required names,
and the public workflow DSL (`step`, `parallel`, and `.named()`). When renaming a
helper, update imports, string-based patches, public exports, consumer examples,
and documentation together; do not rename persisted fields or event names.
