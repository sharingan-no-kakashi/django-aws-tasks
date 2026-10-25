# Phase F implementation and evidence

Phase F operator and telemetry code is present; cloud acceptance is pending. Local verification covers PostgreSQL transactions,
operator interfaces, retention, telemetry, installed artifacts, and a Chromium
consumer journey. No real AWS monitoring deployment or fault-drill result is
claimed. Those external acceptance items remain open.

| Task | Implementation | Verification / outstanding evidence |
|---|---|---|
| DT-029 | Shared audited/idempotent replay, release, dispatch, recovery, per-target sweep | `test_control.py`: concurrent replay, changed intent, new-hold protection, worker/reaper races, audit rollback, publication failure |
| DT-030 | Bounded inspection, problem categories, search, pagination, lineage, unfinished route summaries | `test_inspection.py`: categories, permissions-safe data, two data queries per list, PostgreSQL index plan with 10,000 terminal rows |
| DT-031 | `tasks_list`, `tasks_show`, `tasks_check`, `tasks_workers` | `test_operator_commands.py`: read-only state and publication spies, JSON/error contracts; repeated from an installed wheel |
| DT-032 | `tasks_dispatch`, `tasks_release`, `tasks_replay`, `tasks_recover`, audited bounded `tasks_sweep` | CLI dry runs, request reuse, partial bulk errors, installed-package tests |
| DT-033 | Packaged task/attempt/audit admin, problem and worker pages, custom AdminSite support | `test_operator_admin.py`: view permissions, sensitive fields, escaping, query-backed pages, no raw mutations; installed assets and Chromium consumer journey |
| DT-034 | CSRF-protected previews and signed confirmations using shared services | Permission denial, stale preview, repeat confirmation, signature tampering, custom site, actual-browser replay followed by one business effect |
| DT-035 | Bounded skip-locked detail/audit redaction; retained task and control identities | `test_retention.py`: expiry, old keys, late delivery, protected unfinished work, locked rows, repeat cleanup, audit request identity |
| DT-036 | Bounded structured events, redacted diagnostics, EMF outcome/recovery metrics, per-attempt CloudWatch links | Local metadata/encoding/redaction/dimension tests and idle heartbeat; **actual deployed log-link navigation remains unverified** |
| DT-037 | Monitoring Terraform, alarms, dashboard, shared route pause gates and incident runbook | `test_pause_and_health.py`, mocked Terraform plan tests; **actual alarm firing/clearing and SNS delivery remain unverified; Phase E resources must exist first** |

## Reproduce local verification

Use PostgreSQL 16 and set `POSTGRES_*` and `POSTGRES_DSN` consistently. CI sets
`REQUIRE_POSTGRES=1` so an unavailable service fails instead of skipping required
integration coverage.

```sh
uv sync --locked --all-groups
uv run pytest --junitxml=artifacts/library.xml
(cd examples/sample_project && uv run pytest -m 'not browser' --junitxml=../../artifacts/sample.xml)
uv run --group browser playwright install chromium
(cd examples/sample_project && RUN_BROWSER_TESTS=1 uv run --group browser pytest -m browser --junit-prefix=examples.sample_project --junitxml=../../artifacts/browser.xml)
uv build --no-sources
uv run --group browser python scripts/verify_installed.py --browser --report artifacts/installed.xml
terraform -chdir=terraform/modules/task_observability init -backend=false
terraform -chdir=terraform/modules/task_observability test
```

The installed verifier creates a fresh environment, disables workspace source
resolution, installs the wheel and the sample non-editably, checks packaged
handlers/commands/templates/migrations, and runs integration tests and Chromium
against those installed packages outside the checkout. It removes the temporary
environment on completion. The installed browser report is saved alongside the
integration report as `installed-browser.xml`.

A representative local PostgreSQL 16 plan is retained at
[evidence/phase-f-inspection-plan.json](evidence/phase-f-inspection-plan.json).
It inspected one pending row beside 10,000 successful rows using
`task_record_status_created`, with three shared-buffer hits. This is a small
synthetic query check, not a capacity or latency guarantee. CI can refresh it by
setting `QUERY_PLAN_REPORT=artifacts/inspection-plan.json`.

Requirement assignments live in `verification-matrix.yaml`. The overall strict
release gate still rejects missing AWS and candidate-bound results.
Current assignments and acceptance status are recorded in `acceptance-status.md`. Completing Phase F's local implementation does not complete Phases E,
G, or H, and does not claim that all release gates pass.
