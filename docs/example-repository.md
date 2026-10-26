# Standalone consumer repository

The runnable consumer is a separate `django-aws-tasks-example` checkout. For local
development, place it at `../django-aws-tasks-example`. It owns its Django
application, dependencies, runtime lock, sample tests, LocalStack E2E provisioner,
and AWS deployment/test commands. LocalStack configuration, fixtures, and runners
exist only in the consumer repository. The library keeps reusable runtime code,
Terraform modules, library contract tests, and release evidence gates.

Keep both repositories as siblings for local development. The consumer installs
this checkout as an editable dependency and builds its Lambda image from a wheel
of this checkout. Run `make test`, `make test-browser`, `make dev`, and the local
AWS emulation commands from the consumer checkout. The library Makefile contains
only library maintenance commands. Acceptance and installed artifact scripts
accept `TASK_EXAMPLE_ROOT`.

Library CI still checks consumer compatibility. Set `EXAMPLE_REPOSITORY` to the
published GitHub repository and optionally `EXAMPLE_REF` to the consumer revision.
The default is `sharingan-no-kakashi/django-aws-tasks-example` at `main`.
Consumer CI has matching `LIBRARY_REPOSITORY` and `LIBRARY_REF` settings.

The library’s `scripts/` contains only traceability, release receipts, distribution
verification, and the companion-checkout locator. Consumer deployment, AWS fault
scenarios, load/capacity tools, and LocalStack provisioning live in the example’s
`example_tools` package. Its tooling tests live there too. Library `tests/e2e`
remain release-contract checks and import that companion package via their
explicit checkout setup; ordinary library unit tests do not need it.

Local development uses the example's existing LocalStack SQS/Lambda environment.
The library does not ship a SQL-only backend, process supervisor, local sweeper,
or settings generator. The example's `example_tools.development` module owns
its development orchestration; no alternative SQL-only runner is required.

The library's optional `TaskHarness` remains available for synchronous tests of
the production `DatabaseSqsBackend`. Configure a disposable supported database,
a valid worker route manifest, and `OPTIONS['immediate_dispatch'] = False` before
using it. Commit task acceptance before calling `run_pending()` or `reconcile()`.
These helpers call the shared worker and recovery services without publishing to
SQS; they do not test SQS delivery or Lambda integration. Use the example's
LocalStack tests for those boundaries.
