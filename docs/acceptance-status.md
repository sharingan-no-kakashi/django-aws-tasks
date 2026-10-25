# Implementation and acceptance status

The latest working-tree audit and all remaining tasks are in the single
[consolidated backlog](../todo-task.md), updated 23 October 2026. That audit
observed 351 passing library tests, 33 passing non-browser sample tests, and
clean mypy/lint checks. The subsequent P1 pass registers the versioned WFR
set, restores inventory/format checks, and adds recovery identity, cancellation,
retention, immutable-admin, and headless-startup fixes. See the P1 execution
record in the consolidated backlog for current validation and remaining gaps.
These results do not establish cross-engine or candidate-bound AWS acceptance.

Database qualification: integration CI targets PostgreSQL 15 and 16. MySQL,
MariaDB, and Oracle adapters are experimental until equivalent real-engine
lifecycle, rollback, deduplication, competing-worker, and workflow contract
evidence is available. None is a release-qualified target merely because an
adapter or driver extra exists; PostgreSQL remains subject to the beta
candidate gate as well.

The area summary below originated on 19 October and includes subsequent
implementation updates. Treat its pending-verification descriptions as context;
use the consolidated backlog for current work and the audit's exact test scope.

| Area | Implemented | Evidence still required |
| --- | --- | --- |
| Backend ownership | Backend-scoped claims, dispatch discovery/reservations, reaping, holds, health, and `TASK_BACKEND_ALIAS` selection | Multi-backend regressions run locally; AWS candidate run pending |
| Local execution | Shared eligibility/version/route checks, validated lease/limits, deadline interruption, handler budget API | Local integration and budget regressions |
| Corrupt history | Read-only diagnostics, refused ambiguous recovery/finalization, invariant alarm, audited review command | Local corruption/audit regressions; deployed alarm pending |
| Schema | One initial migration per app includes current fields, constraints, audit records, and indexes; no earlier development upgrade path | Fresh PostgreSQL installs; support matrix CI execution |
| Sample route policy | Documents-only document task | Invalid override rejected in consumer regression |
| Configuration | Distinct and consistent source identities, explicit module allowlist, policy/route membership, writer/version checks at invocation entry | Local validation regressions; configured CI support matrix |
| Time and telemetry | Database time for due/retry decisions; refreshed invocation budgets; application/package versions, publication tokens, attempt/task correlation | Local checks; AWS stream metadata resolution scenario pending |
| Deployed failure cases | Initialization outage through DLQ and delivery hold, audited release/replay/conflict, browser permissions, remote-provider response loss, direct multi-record interruption, two-image additive rollout and rollback | All require a disposable AWS execution |
| Failure assignments | FM-027, FM-028, FM-029 now have executable test assignments | Baseline remains batch size one; batch tests are explicitly direct-event fixtures |
| Operational example | Subscribed SNS-to-SQS alarm inbox, optional owned email subscription, independent signing secret, credential refresh, hashed dependency lock | Fresh apply, delivered notifications, secret/connection behavior on AWS |
| Capacity | Burst and sustained experiments at three concurrency budgets, CPU/memory task mix, database/backlog/queue series, outcome/latency acceptance, regional usage-cost inputs | Real measurements and cost inputs; no capacity or cost result claimed |
| Release | Exact wheel/sdist candidate hashes and source identity, fresh per-suite receipts, full strict gate, reusable AWS workflow, separate Trusted Publishing action | Complete CI/AWS/capacity evidence, PyPI project/environment setup, release rehearsal |
| Workflows (optional) | Static DAG orchestration: transactional/idempotent submission, output references, failure propagation, preparation-failure isolation, audited retry and cancellation, display names, status/output APIs, telemetry, retention protection, `workflows_list`/`workflows_show`/`workflows_retry`/`workflows_cancel`/`workflows_sweep`/`workflows_cleanup`, read-only admin with reviewed recovery | Real-execution lifecycle coverage on non-PostgreSQL engines, measured overhead limits, and installed/deployed AWS evidence |

The release workflow requires Python 3.12–3.14, PostgreSQL 15 and 16, and minimum
and latest supported dependency cases. Merely defining those jobs is not evidence
that every matrix cell has passed. The current working tree is beta. Release qualification still requires the
candidate gate to succeed. Publishing remains a distinct action.

See [backend reference](backend-reference.md), [operations](operations.md),
[release procedure](release.md), and the [AWS sample guide](../../django-aws-tasks-example/terraform/README.md).

## Local verification after the fixes

The local verification includes PostgreSQL 15 and 16, the library and consumer
regressions, real Chromium recovery, clean wheel/sdist installation outside the
checkout, provider-mocked Terraform modules and both examples, and Actions lint.
Both digest-pinned Lambda images build with hash-locked dependencies. A local
cross-image smoke check persisted old queued/held work, applied the additive
migration/import shim, released and executed the work, and executed newer
producer work after rollback. These are local/container observations, not AWS
service evidence. Temporary verification databases are isolated from existing
application databases.

Real AWS fault delivery, notification receipt, rollout, measured capacity/cost,
and publishing remain external acceptance steps. The repository defines those
checks and fails the candidate gate when their evidence is absent; no successful
cloud run or published release is claimed.
