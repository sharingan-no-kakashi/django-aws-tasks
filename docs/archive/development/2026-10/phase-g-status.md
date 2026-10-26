# Phase G implementation and evidence

Consumer deployment/scenario tools referenced below now live in the standalone
example repository’s `example_tools/` package. The original evidence remains
historical; moving the tools does not establish new AWS acceptance.

Phase G runtime and verification code is implemented; acceptance remains conditional on observed results. Local verification covers the injected failure
harness, the seeded transition model, concurrency races, reconciliation budget,
fresh-schema installation, and current import-path resolution. Real AWS fault drills (actual
timeout/OOM termination, DLQ, failover, delivery boundaries, capacity/cost) are
not claimed. Those external acceptance items remain open until the disposable
AWS workflow and capacity run produce retained results.

| Task | Implementation | Verification / outstanding evidence |
|---|---|---|
| DT-038 | Cross-boundary injected faults + traceability inventory | `test_failure_boundaries.py`; `scripts/check_traceability.py` (94 requirements valid) |
| DT-039 | Seeded transition oracle + multiprocess stress | `test_state_machine.py` + `tests/support/state_machine.py`; `test_process_races.py` |
| DT-040 | Installed wheel/sdist, fresh schemas, admin/CLI | `tests/artifact/test_install.py`, `tests/unit/test_migrations.py`, `scripts/verify_installed.py`; operator-artifacts CI |
| DT-041 | AWS delivery/routing/acknowledgment boundaries | `tests/e2e/test_aws_delivery.py`, `test_infrastructure.py`; **requires AWS** |
| DT-042 | Real timeout/OOM/abandoned-lease recovery | `tests/e2e/test_aws_termination.py` + `scripts/aws_faults.py`; **requires AWS** |
| DT-043 | DLQ, delivery holds, operator recovery | `aws_faults.stopped_route`/`reconciliation_outage`; **requires AWS** |
| DT-044 | Current task resolution, database failover | `aws_faults.database_failover`, `test_task_resolution.py`; **failover requires AWS Multi-AZ** |
| DT-045 | Capacity load + cost inputs | `scripts/load_sample.py`; **requires AWS; cost remains unpriced** |

## Required deployed assignments

FM-027 and FM-028 now map to deployed direct-event batch fixtures, while FM-029
maps to the two-image rollout test. Production mappings remain batch size one;
see the explicit fixture scope in `implementation-spec.md`. The extended suite
also covers initialization-to-DLQ/delivery-hold recovery, CLI and browser
operator actions, remote-provider acceptance/response loss, delivered alarm
notifications, and actual saved log metadata resolution. These scenarios have
implementations; AWS execution evidence remains pending. See `acceptance-status.md`.

## Reproduce local verification

```sh
uv run pytest tests/integration/test_failure_boundaries.py tests/integration/test_state_machine.py \
  tests/integration/test_process_races.py tests/integration/test_reconciliation_budget.py \
  tests/unit/test_migrations.py tests/integration/test_task_resolution.py
uv run python scripts/check_traceability.py
```

The deployed fault drills run under `aws-evidence.yml` with `--enable-faults`;
fault controls are gated by `Disposable=true` plus a matching `RunId` tag and
`TASK_SAMPLE_FAULTS=1`, and never from untrusted task payloads. Completing Phase
G's local implementation does not complete Phase H, and does not claim
release-gate evidence until the AWS workflow and the capacity run produce
retained results.
