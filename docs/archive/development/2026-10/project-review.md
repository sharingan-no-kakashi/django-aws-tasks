> Historical review notice: findings describe the tree at review time.
> The historical [backlog](../../backlogs/2026-10-23/todo-task.md) records subsequent P1 fixes and remaining evidence.

# Design implementation review — 19 October 2026

> Historical review. The follow-up implementation and outstanding external
> acceptance are tracked in the single [consolidated backlog](../../backlogs/2026-10-23/todo-task.md),
> with context in [acceptance-status.md](acceptance-status.md).

## Assessment

The project has a substantial working implementation of the architecture. The
database protocol, package-owned handlers, local consumer, operator tools,
Terraform modules, and artifact verification are present. It is not yet a
complete implementation of the design's acceptance contract or ready for a
first supported release.

The remaining work includes reproducible correctness defects, missing failure
scenarios, operational integration, measured capacity evidence, and the release
pipeline. It cannot be completed merely by running the existing AWS workflow.

This review used `Django tasks design.md` as the normative design,
`docs/implementation-spec.md` as the frozen initial contract, and `todo-task.md`
as the acceptance backlog. It inspected runtime, models/migrations, admin/CLI,
sample, Terraform, verification scripts, and CI. It did not modify runtime code
or deploy AWS resources. Findings describe the current working tree, which
contains substantial uncommitted work; they are not tied to a clean Git commit.

## Verification performed

An isolated PostgreSQL 16 container was used; existing application databases
were not used for tests or reproductions.

| Check | Result |
| --- | --- |
| Library unit/integration/concurrency suite | 223 passed |
| Sample integration suite | 18 passed; browser case selected separately |
| Checkout Chromium operator journey | 1 passed |
| Ruff lint and format | Passed; 144 Python files formatted |
| Backend wheel and source distribution build | Both built; wheel built from sdist |
| Clean wheel installation outside checkout | 159 integration tests and browser journey passed |
| Clean sdist installation outside checkout | 159 integration tests and browser journey passed |
| Task-system mocked Terraform suite | 6 passed |
| Observability mocked Terraform suite | 2 passed |
| Terraform-to-runtime manifest and IAM checks | 2 passed |
| Both example Terraform roots | Validated |
| Requirement inventory | 94 IDs structurally valid |
| Strict traceability with local suite reports | Failed, as expected: unassigned rows and missing mandatory evidence |
| AWS deployment, fault drills, load, billing | Not run; no deployed behavior or cost claim made |

The initial library/sample runs interfered because both used the same temporary
test database name. They were rerun sequentially with separate names; the
passing counts above are from those corrected runs. Offline clean-install
attempts initially lacked cached dependencies; the wheel verification passed
after fetching those dependencies. These were review-environment issues.

JUnit reports and candidate distributions were written under `/private/tmp/`
with the prefix `django-aws-tasks-review-`. The build candidates are in
`/private/tmp/django-aws-tasks-review-dist/`. Local browser runs warn that
collected static files are absent; the browser journeys nevertheless pass.

## Confirmed implementation gaps

### R1 — P1: Reconciliation does not respect backend ownership

**Locations:** `src/django_tasks_aws/services/reconcile.py:30`, `:72`, `:90`,
`:238`; `src/django_tasks_aws/services/dispatch.py:109`;
`src/django_tasks_aws/management/commands/tasks_worker.py:30`.

Recovery and dispatch candidate queries filter by state and sometimes queue,
but not `backend_alias`. Exhausted-budget cleanup is database-wide. Publication
chooses the queue from the caller's manifest rather than resolving the stored
backend. The Lambda handlers also always choose the default backend.

**Reproduced:** A task stored under `another_backend`, on a queue also present
in the default backend, was published by default-backend reconciliation using
the default backend's queue configuration. Its legitimate backend's delivery
budget was consumed. The receiving default Lambda would reject that task's
backend identity; repeated wrong publication can lead to holds. Expired tasks
can similarly be recovered under the wrong ambiguity policy.

**Required:** Thread backend identity through discovery, reservations, recovery,
health, and local-worker selection; bind publication to the stored backend and
trusted manifest. Provide an explicit Lambda backend selection mechanism, or
reject/document unsupported multiple-backend configurations at startup.
Verify two backends sharing a database and route names with different queue
identities and recovery policies.

**Design:** §§3.1, 4.1, 6.3, 11; DT-006, DT-010, DT-017.

### R2 — P1: The local worker bypasses compatibility gates

**Locations:** `src/django_tasks_aws/management/commands/tasks_worker.py:21`;
`src/django_tasks_aws/services/execution.py:31`.

The command invokes claim/execution directly. It does not apply the Lambda
worker's supported-payload-version and task-policy route checks. It also accepts
an unchecked lease override and provides no process execution deadline.

**Reproduced:** An enqueued `record_event` whose stored payload version was
changed to `99` executed successfully through `tasks_worker`. The Lambda worker
would have placed it on an incompatible-payload hold.

**Required:** Share the eligibility/compatibility protocol between adapters.
Validate command bounds and lease overrides. Define and enforce the local
execution lifetime, including safe behavior when work exceeds its lease.
Execution currently checks the token before the body but not lease expiry;
the Lambda lifetime assumption does not automatically protect a local process.

**Design:** §§6, 8, 9, 14.6; DT-020 explicitly requires local lease/route policy.

### R3 — P1: Invariant diagnostics are missing; recovery guesses a repair

**Locations:** `src/django_tasks_aws/services/reconcile.py:238`;
`src/django_tasks_aws/management/commands/tasks_check.py:15`.

There is no pass checking that a running task has its matching current attempt,
or reporting mismatched task/attempt histories. `tasks_check` runs configuration
checks and problem categorization, rather than these database invariants.
Recovery updates the attempt without checking that an attempt row was found.

**Reproduced:** A running task with an expired lease, one counted attempt, and
zero attempt rows became unheld pending work with zero attempt rows under a
retry-safe policy. The system scheduled another execution instead of surfacing
the corruption and preserving it for audited investigation.

**Required:** Detect absent/mismatched current attempts and impossible history
before automatic recovery. Emit explicit invariant diagnostics and health
signals. Refuse ambiguous repairs and provide the audited operator procedure
required by the design.

**Design:** §11.5; DT-017, DT-018, DT-038.

### R4 — P2: The schema does not require held tasks to be pending

**Location:** `src/django_tasks_aws/models.py:137`.

The hold constraint checks that both fields are present or absent, but does
not require `status = PENDING` when present. TaskAttempt status/outcome fields
also have choices without database checks enforcing their finite values.

**Reproduced:** PostgreSQL accepted a `SUCCESSFUL` task with a non-null hold
timestamp and ambiguity hold reason.

**Required:** Add an additive migration enforcing the hold-state relation and
the specified finite attempt values. Review existing rows before introducing
constraints. Pair these protections with R3's diagnostics for relations that
cannot be expressed as simple row checks.

**Design:** §§4.2–4.3; DT-005.

### R5 — P2: The sample's document task is not restricted to documents

**Location:** `examples/sample_project/sample_project/settings.py:148`.

The task docstring promises a documents-only task, but its policy omits
`allowed_routes`, so the default permits both routes. The existing test only
proves that a task enqueued with the default documents route is not selected
by a short worker; it does not test an invalid producer override.

**Reproduced:** `render_document.using(queue_name="short").enqueue(...)`
was accepted, executed on short, and created its business effect.

**Required:** Declare the documents-only policy and verify rejection before
acceptance. Keep the ordinary event task routable to both workers.

**Design:** §3.1; DT-019; DS-008's consumer demonstration.

### R6 — P2: Startup validation accepts conflicting queue identities

**Locations:** `src/django_tasks_aws/schemas.py:88`;
`src/django_tasks_aws/checks.py:29`.

Profiles are checked independently; there is no cross-profile uniqueness check
for source ARN/URL or consistency check between them. System checks primarily
compare manifest keys with `QUEUES`. They do not establish the configured
database's PostgreSQL/writer suitability or validate policy routes against the
manifest. Lambda initialization does not invoke Django's system-check command.

**Reproduced:** Two routes using exactly the same source ARN and URL load
successfully. This can undermine the intended queue/function isolation and
produce persistent wrong-route deliveries.

**Required:** Validate the complete configuration at its runtime boundary,
including distinct source identities, route/policy membership, required database
properties, and supported backend selection. Checks should return actionable
configuration errors for malformed structures, rather than incidental exceptions.

**Design:** §§2–3, 6, 14.2; DT-006.

### R7 — P2: Deadline and database-clock handling is incomplete

**Locations:** `src/django_tasks_aws/services/worker.py:124`, `:154`;
`src/django_tasks_aws/services/execution.py:58`;
`src/django_tasks_aws/services/reconcile.py:259`.

The worker snapshots remaining time before record processing, then uses that
snapshot for its claim decision. It does not refresh it after database/import
work or expose a supported execution-budget interface to handlers before
expensive operations. The early-message shortcut uses application time, while
the SQL claim uses database time. Retry timestamps also use application time.

**Consequence:** A lagging application clock can acknowledge database-due work
as early, adding redispatch delay. An advanced clock can schedule retry earlier
or later than intended. Slow pre-execution work can consume the finalization
allowance after its one-time check.

**Required:** Use database-relative due/backoff decisions consistently. Refresh
the invocation budget near claim and execution, and define how consumers bound
downstream calls with the remaining time. This finding is based on code paths;
no AWS deadline behavior was exercised in this review.

**Design:** §§8.3–8.4, 10.2, 17.2; DT-012, DT-014, DT-016.

## Missing acceptance work

### A1 — P1: The deployed failure suite does not implement all claimed scenarios

**Locations:** `tests/e2e/`, `scripts/aws_faults.py:90`,
`docs/phase-g-status.md`.

The AWS suite implements happy paths, rollback, duplicate delivery, missed
publication, abrupt termination, a brief stopped mapping, heartbeat outage,
database failover, and infrastructure identity checks. That is useful coverage.
However, it does not yet implement:

- Broken worker initialization lasting through actual source redrive/DLQ arrival
  and durable `delivery_exhausted` hold.
- Repair followed by audited release, terminal replay, repeated/conflicting
  requests, and preserved operation identity through deployed admin and CLI.
- An outage long enough to span redispatch/visibility intervals and controlled
  bulk recovery. The stopped-route helper restores the mapping after one
  immediate observation of zero attempts.
- A genuine additive deployment with old queued/held payloads, compatibility
  imports, unsupported-version holds, and rollback.
- A controllable external provider that accepts an operation and loses its
  response. Current protected effects are database rows; simply raising
  `AmbiguousOutcome` or killing a process after a database effect does not
  demonstrate a remote provider's idempotency/acceptance contract.
- Deployed log-link navigation and notification delivery to an actual alarm
  subscriber. The heartbeat drill checks alarm state, not SNS receipt.

The phase status should distinguish implemented local coverage, implemented AWS
scenarios awaiting execution, and scenarios that still need implementation.

**Design:** §§10.3, 13, 18.2, 18.5; DT-041–DT-044, DT-036–DT-037.

### A2 — P1: Three mandatory failure rows remain unassigned

**Location:** `docs/verification-matrix.yaml:424`.

FM-027 (one failed batch record), FM-028 (death partway through a batch), and
FM-029 (old queued/held payloads across deployment) have no test IDs. Strict
traceability correctly rejects them. There is a local partial-batch response
check, but no full deployed coverage of the batch cases.

The frozen baseline explicitly uses batch size one while the normative failure
matrix still requires the batch experiments. Resolve that contract explicitly:
use a dedicated non-baseline verification fixture or revise the supported scope
and requirement disposition. Do not silently waive those rows. FM-029 remains
necessary regardless of the batch decision.

### A3 — P1: No candidate-bound release gate or publishing workflow exists

**Locations:** `.github/workflows/ci.yml:19`,
`.github/workflows/aws-evidence.yml`, `scripts/check_traceability.py:49`.

CI checks the non-strict inventory. AWS verification is manual-only. There is
no workflow aggregating all required reports with strict traceability and no
dry release, evidence manifest, or Trusted Publishing workflow.

The existing strict checker matches test names and pass/fail results, but does
not bind reports to a commit, artifact hashes, image digest, dependency matrix,
infrastructure revision, or freshness. Passing old XML is not candidate proof.
The fixed 94-ID inventory also does not directly cover all operational,
configuration, deadline, load, security, and release acceptance criteria.

**Required:** Produce one identified candidate and an evidence manifest; collect
required suite and support-matrix results; reject missing, stale, skipped, or
failed mandatory evidence; include cleanup; upload the exact tested bytes only
after the gate passes. Add scheduled/relevant-change AWS verification with the
appropriate environment controls. Publication remains a distinct later action.

**Design:** §§14.7, 18.4, 20; DT-038, DT-048–DT-050.

### A4 — P1 for workload claims: Capacity and cost are not established

**Location:** `scripts/load_sample.py:80`.

The loader submits a configurable burst and captures start/completion
percentiles and basic outcomes. It uses the same inexpensive database-effect
task on both routes. Its cost total is intentionally null, and it is not part
of the AWS workflow.

Missing work includes representative duration/resource distributions, sustained
load, multiple concurrency settings, timestamped database sessions/waits and
backlog/queue series, provider rate behavior, duplicate publication counts,
outage recovery and controlled releases, and usage-based regional cost inputs.
Tasks that finish failed or held do not fail the load command unless work
remains pending, so workload acceptance also needs explicit outcome thresholds.

**Required:** Define workload and success criteria, extend instrumentation, run
the burst and sustained/outage cases, and retain a measured capacity envelope
and cost report. A billing API integration is not mandatory; traceable usage
and stated regional assumptions are.

**Design:** §§2, 17, 18.3; DT-045.

### A5 — P2: Telemetry omits required correlation and release information

**Locations:** `src/django_tasks_aws/telemetry.py:13`;
`src/django_tasks_aws/backends.py:62`;
`src/django_tasks_aws/services/dispatch.py:148`.

The event allowlist excludes task import path, attempt ordinal, dispatch token,
and next-eligibility timestamp. Ordinary events do not consistently include
environment and release/invocation identity. The producer version is always the
backend package version, not the application release that created the payload.
Worker release identity is richer, but cannot recover the producer revision.

**Required:** Include the design's safe correlation fields and distinguish
package, producer-application, and worker-application versions. Emit stale
finalization/obsolete publication diagnostics. Retain deployment/tool/database
versions and log-navigation evidence in candidate results.

**Design:** §§4.1, 15.1–15.2, 18.4; DT-036.

### A6 — P2: The deployment example needs operational completion

**Locations:** `examples/sample_project/terraform/bootstrap/main.tf:108`;
`examples/sample_project/Dockerfile.lambda:16`;
`examples/sample_project/sample_project/deployment.py`.

The bootstrap creates an SNS topic but no subscription or configured existing
notification destination, so a fresh deployment has no delivered alarm recipient.
The Lambda image resolves broad dependency ranges with pip instead of consuming
the committed application lock. An image digest identifies the built image,
but does not make a later rebuild use the same dependencies.

**Required:** Connect an owned notification destination and verify delivery;
build the application image from a locked, recorded dependency set; document
network/egress ownership and cost inventory, connection budgets, deployment
compatibility and rollback, and database restore relative to external effects.
Review secret rotation behavior: sample database credentials are loaded at
process initialization, and the fallback Django secret derives from the database
password. These choices need explicit operational treatment before a supported
deployment profile is claimed. They are not demonstrated AWS failures here.

**Design:** §§14.1–14.4, 15.3, 17; DT-021, DT-024, DT-046–DT-047.

### A7 — P2: Support and status documentation need reconciliation

CI already has installed-artifact jobs for Python 3.12, 3.13, and 3.14 and a
minimum-dependency case. The frozen spec's statement that CI uses one Python
version is stale. PostgreSQL 15 is advertised but CI uses 16; add minimum-server
coverage or narrow the claim based on evidence. Do not interpret successful
tests on this review's Python 3.12/PostgreSQL 16 environment as full matrix proof.

The backlog has all acceptance boxes unchecked, while phase notes describe
whole phases as implemented despite the missing scenarios above. Add a current
acceptance-status table with evidence links, distinguish code from executed
acceptance, and provide the package configuration/reference and release
documentation required by DT-047. The architecture document should remain the
protocol reference rather than the only way to learn all backend options.

## Work order to reach the design

1. Fix R1–R6 with focused regression coverage: backend ownership, local protocol
   parity, corruption detection, schema checks, sample restriction, configuration.
2. Complete deadline/clock behavior and correlation/version telemetry (R7, A5).
3. Implement missing deployed fault/provider/operator/rollout scenarios and
   resolve all failure-matrix assignments (A1–A2).
4. Complete the operational example, then run a disposable fresh deployment,
   all mandatory AWS scenarios, notification checks, and verified teardown (A6).
5. Run the representative capacity/connection/outage experiments and retain
   measured limits and cost inputs (A4).
6. Reconcile support/status/reference documentation and build the candidate-bound
   release gate, exact-artifact rehearsal, and Trusted Publishing configuration
   (A3, A7). Publish only as the separate, explicitly authorized final action.

The public scope should continue to exclude async task bodies, priority/FIFO
ordering, workflows, cross-database atomic acceptance, arbitrary long-lived
workers, and exactly-once remote effects. These are explicit exclusions, not
missing implementations to add in this completion effort.
