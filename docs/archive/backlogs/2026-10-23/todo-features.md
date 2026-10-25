> Historical snapshot, archived 23 October 2026. The only active backlog is
> [todo-task.md](../../../../todo-task.md). Checkboxes below preserve the original plan.

# Developer experience and feature backlog

Updated 23 October 2026. This backlog breaks the 15 proposed improvements into
45 tasks. It complements the [core implementation backlog](todo-task.md) and
the [workflow backlog](todo-workflows.md).
The [LocalStack backlog](todo-localstack.md) defines the separate emulated AWS
E2E tier used to verify service integration.

These are planned additions. They do not expand the first release's mandatory
scope automatically. Select features for a release explicitly, then add their
requirements and evidence to the release gate. Existing correctness and release
requirements remain in force.

Some tasks extend capabilities already present: local workers, configuration
checks, submission deduplication, delayed tasks, and inspection. Preserve those
APIs while adding the proposed behaviour. Command and API names below describe
future interfaces unless they already exist in the package.

| Feature | Tasks | Suggested stage |
| --- | --- | --- |
| Supported local development profile | FT-001–FT-003 | Next |
| Configuration scaffolding | FT-004–FT-006 | Next |
| Deployment diagnostics | FT-007–FT-009 | Next |
| First-party testing helpers | FT-010–FT-012 | Next |
| Task progress reporting | FT-013–FT-015 | Then |
| Recurring tasks | FT-016–FT-018 | Then |
| Task expiry and deadlines | FT-019–FT-021 | Then |
| Simpler idempotent submission API | FT-022–FT-024 | Then |
| Bulk submission | FT-025–FT-027 | Then |
| Pre-deployment compatibility reports | FT-028–FT-030 | Next |
| Provider cooldowns and shared rate limits | FT-031–FT-033 | Optional, driven by provider needs |
| Tenant isolation and fair scheduling | FT-034–FT-036 | Optional, for shared SaaS deployments |
| Artifact-reference helpers | FT-037–FT-039 | Optional, for document/data workloads |
| Broader Django compatibility | FT-040–FT-042 | Conditional on feasibility and demand |
| External-effect recovery recipes | FT-043–FT-045 | Then |

Dependencies are prerequisites for completing a task. A referenced WF task
belongs to the workflow backlog. Runtime tasks include meaningful tests as part
of implementation; later verification tasks collect installed-artifact and
end-to-end evidence. Database ownership and concurrency claims require real
supported engines and independent connections. Each accepted feature needs
documentation, migration guidance where relevant, and evidence for its claimed
support matrix.

## 1. Supported local development profile

### FT-001: Define and implement local worker configuration

**Dependencies:** None.

Add an explicit local profile that requires no queue ARNs or URLs. Reuse the
durable database protocol and route policies. Keep production queue validation
strict, and reject a local profile at deployed Lambda entry points.

- [ ] **Acceptance:** An existing Django app can enqueue and execute work
  locally without AWS credentials, placeholder cloud identities, or AWS client
  requests. Invalid deployed configuration still fails validation.

### FT-002: Supervise local workers and reconciliation

**Dependencies:** FT-001.

Provide a foreground development runner for configured routes and periodic
reconciliation with publication disabled. Support code reload, bounded polling,
worker error reporting, and clean process shutdown. Preserve hard execution
deadlines and lease recovery after abrupt worker death.

- [ ] **Acceptance:** The runner processes both routes, recovers an abandoned
  claim, and advances enabled workflows. Reload and shutdown leave no orphan
  worker processes. Publication-disabled reconciliation never contacts SQS.

### FT-003: Verify the clean local journey

**Dependencies:** FT-001, FT-002.

Document installation into an existing application and update the sample to
use the supported profile. Show a completed task, an automatic retry, a hold,
and recovery. Explain which database and Docker prerequisites are required.

- [ ] **Acceptance:** A clean consumer installation follows the documented
  commands without AWS access. Automated smoke checks verify the expected
  outcomes and startup failures produce instructions a developer can act on.

## 2. Configuration scaffolding

### FT-004: Generate application settings and starter policies

**Dependencies:** FT-001.

Generate a settings snippet, module allowlist, named routes, and policy examples
from a small set of explicit inputs. Support local and deployed profiles.
Preserve application task arguments and existing settings; report generated
files and conflicts.

- [ ] **Acceptance:** Generated settings pass package checks. Repeating
  generation is predictable and does not overwrite edited application files
  silently. Local-only values cannot become a valid production manifest.

### FT-005: Provide an existing-infrastructure deployment recipe

**Dependencies:** FT-004.

Add a recipe that uses an existing database, VPC, subnets, and secret references
with the reusable Terraform module. Explain application permissions, image
packaging, migrations, and route-manifest injection. Keep the full sample stack
as a separate reproducible example.

- [ ] **Acceptance:** A consumer can identify the resources the module creates
  and those it reuses. Plans do not create or replace the supplied database or
  VPC, and outputs configure matching producers, workers, and sweepers.

### FT-006: Exercise generated projects outside the checkout

**Dependencies:** FT-003, FT-004, FT-005.

Create a clean consumer from the generated files, install the built package,
apply migrations, and execute work. Verify Terraform examples with mocked
providers and retain a separately identified deployed smoke scenario.

- [ ] **Acceptance:** The generated project works without editable imports or
  copied coordination code. Both local setup and deployment instructions have
  reproducible checks; mocked infrastructure results are labelled separately
  from actual deployment evidence.

## 3. Deployment diagnostics

### FT-007: Define diagnostic results and exit behaviour

**Dependencies:** None.

Extend the existing inspection model with structured diagnostic results for
configuration, schema, connectivity, credentials, and deployed resource drift.
Distinguish a failed check from a check that could not run with available
permissions. Define stable JSON output and useful exit codes.

- [ ] **Acceptance:** Every diagnostic has a target, outcome, evidence summary,
  and suggested next action. Existing read-only `tasks_check` behaviour remains
  compatible, and unavailable evidence is never reported as a successful check.

### FT-008: Implement local and cloud diagnostic checks

**Dependencies:** FT-007.

Check migration state, writable database suitability, route consistency, and
accessible queue/function configuration. Use bounded requests and disclose
which permissions a check requires. Keep default diagnostics read-only; any
end-to-end canary must be a separately selected operation with visible results.

- [ ] **Acceptance:** Diagnostics identify a missing migration, unreachable
  database, incorrect queue binding, and incompatible worker profile. Default
  execution creates no task, queue message, audit mutation, or cloud resource.
  Secrets are excluded from reports.

### FT-009: Validate failure reports and document repairs

**Dependencies:** FT-007, FT-008.

Exercise restricted permissions, unavailable services, partial connectivity,
and stale resource configuration. Add incident examples linking reports to
existing inspection, release, and deployment commands.

- [ ] **Acceptance:** Tests cover successful, failed, and unavailable checks.
  Deployed checks produce the documented results under a restricted role, and
  reports remain bounded when several resources fail at once.

## 4. First-party testing helpers

### FT-010: Package isolated backend fixtures

**Dependencies:** None.

Provide opt-in pytest helpers for isolated database configuration, captured SQS
publication, deterministic retry backoff, and bounded worker/reconciliation
execution. Keep test dependencies out of the production dependency set.

- [ ] **Acceptance:** Fixtures isolate parallel test runs, restore process-wide
  configuration and transports, and never require real AWS access. Database
  fixtures preserve the real transaction and lease protocol.

### FT-011: Add assertions for durable task behaviour

**Dependencies:** FT-010.

Provide assertions and examples for accepted work, rolled-back submissions,
duplicate submissions, attempts, holds, and completed workflows. Make commit
boundaries explicit. Use database time for ownership checks rather than a fake
clock that bypasses the coordination protocol.

- [ ] **Acceptance:** Examples detect a missing task, an unexpected duplicate
  effect, and premature execution. Tests using real workers prove retry and
  recovery behaviour without modifying task rows to manufacture success.

### FT-012: Verify helpers from installed distributions

**Dependencies:** FT-010, FT-011.

Run the documented testing examples in an independent consumer using the wheel
and source distribution. Explain when Django's immediate/dummy backends are
sufficient and when tests need the real durable backend.

- [ ] **Acceptance:** Installed helpers exercise rollback, retries, and recovery
  on the supported test matrix. Omitting required database services produces a
  clear failure in mandatory CI, and documented imports are public APIs.

## 5. Task progress reporting

### FT-013: Define attempt-scoped progress records

**Dependencies:** None.

Define bounded progress fields such as stage, completed units, total units,
message, and update time. Associate progress with its execution attempt and
document how a new attempt resets or supersedes earlier progress. Keep progress
separate from completion state and lease renewal.

- [ ] **Acceptance:** An additive migration preserves existing tasks. Invalid
  counters and oversized messages are rejected. A progress record cannot mark
  work successful or extend the owner's lifetime.

### FT-014: Implement ownership-checked progress updates

**Dependencies:** FT-013.

Expose progress reporting through the active task context or a documented
helper. Fence updates by current attempt identity and database-clock expiry.
Bound update frequency and make the behaviour of repeated updates explicit.

- [ ] **Acceptance:** A current attempt can report progress; an obsolete or
  expired attempt cannot overwrite a newer attempt's report. Races with
  finalization and recovery are tested on real database connections.

### FT-015: Display progress through inspection and the sample

**Dependencies:** FT-014.

Add progress to result inspection, admin, and the sample's task detail page.
Use bounded polling or another documented refresh mechanism. Show stale and
previous-attempt progress clearly, and measure the write/read overhead.

- [ ] **Acceptance:** An import example displays advancing unit counts without
  manual refresh. A retry visibly starts a new attempt, default output remains
  redacted, and progress reporting does not dominate database activity.

## 6. Recurring tasks

### FT-016: Specify schedules and durable occurrence identity

**Dependencies:** None.

Define interval and cron schedules, timezones, daylight-saving behaviour,
pause/resume, overlap rules, and bounded missed-run policies. Give each nominal
occurrence a durable identity based on schedule, revision, and scheduled time.
Define operation keys for separate intended occurrences.

- [ ] **Acceptance:** Examples specify what happens after duplicate triggers,
  an outage, an overlapping run, and a daylight-saving transition. Separate
  occurrences cannot accidentally share one business-effect identity.

### FT-017: Implement schedule management and durable submission

**Dependencies:** FT-016, FT-022, FT-023.

Persist schedule definitions and occurrence history. Integrate EventBridge
Scheduler with a package-owned submission entry point that applies the normal
transaction, import allowlist, route, and deduplication rules. Reconcile missed
occurrences according to the selected bounded policy.

- [ ] **Acceptance:** Repeated or concurrent triggers create one logical task
  per occurrence. Pause and overlap decisions survive process restarts, and
  catch-up does not produce an unbounded burst after a long outage.

### FT-018: Expose and verify recurring-task operations

**Dependencies:** FT-017, FT-007.

Provide schedule listing, upcoming occurrences, pause/resume, and occurrence
inspection through commands and admin. Add local deterministic schedule cases
and an actual scheduler-delivery scenario to the selected release evidence.

- [ ] **Acceptance:** Developers can identify whether an occurrence was
  submitted, skipped, combined, or still outstanding. Timezone and missed-run
  cases pass, and scheduler delivery timing is distinguished from task start.

## 7. Task expiry and deadlines

### FT-019: Define latest-start and execution-deadline semantics

**Dependencies:** None.

Define an optional latest-start time separately from `run_after` and the worker
timeout. Specify how an absolute execution deadline reduces the available
business budget. Preserve ambiguity handling when effects may already have
occurred; elapsed time alone cannot prove an external operation was aborted.

- [ ] **Acceptance:** The contract distinguishes work that expired before any
  attempt from a running attempt whose budget ended. Impossible scheduling
  windows are rejected or explicitly represented, and existing tasks retain
  their current behaviour.

### FT-020: Enforce expiry through the normal state machine

**Dependencies:** FT-019.

Persist deadlines, check them atomically when claiming, suppress obsolete
publication, and reconcile expired pending work to an inspectable terminal
outcome. Apply the smaller execution budget without weakening fixed-lease
ownership or inventing confirmed failure after an uncertain effect.

- [ ] **Acceptance:** Late delivery cannot start expired work. Expired pending
  tasks do not remain permanently stranded, and expiry does not create a
  handler attempt. Running deadline exhaustion follows the declared ambiguity
  policy and never authorises overlapping ownership.

### FT-021: Verify deadline behaviour across retries and workflows

**Dependencies:** FT-020, FT-011; WF-003.

Test retry dates beyond expiry, delayed submissions, clock skew, stale messages,
and finalization near deadline. Define how expiry propagates through workflow
dependencies and how an operator can inspect or explicitly replace obsolete
work.

- [ ] **Acceptance:** Database-clock boundary and concurrency cases pass.
  Expired parents have documented descendant outcomes, operator replacement is
  audited, and the sample distinguishes expiry from handler failure.

## 8. Simpler idempotent submission API

### FT-022: Design a public submission interface

**Dependencies:** None.

Specify a typed submission interface around existing backend deduplication.
Keep task arguments separate from submission metadata and distinguish the
submission key, deduplication scope, and business operation key. Retain ordinary
`Task.enqueue()` usage.

- [ ] **Acceptance:** Examples fit an existing Django business transaction and
  do not reserve keywords that a task function may need. Repeated and changed
  intent, result expiry, and operation-key comparison have explicit semantics.

### FT-023: Implement submission through existing coordination services

**Dependencies:** FT-022.

Use the existing enqueue, uniqueness, payload, policy, and on-commit publication
services. Preserve accepted identities across retries and operator replay.
Translate conflicts into documented errors identifying the original task.

- [ ] **Acceptance:** Concurrent identical submissions return one task.
  Changed payload, routing, or explicit intended operation conflicts according
  to the contract. Rollback creates no executable work, and no network call
  occurs inside the business transaction.

### FT-024: Verify and migrate submission examples

**Dependencies:** FT-023, FT-011.

Update sample and user examples to use the simpler interface where appropriate.
Keep the existing backend method compatible. Exercise scope/key limits,
retained identities, and submission to each named route.

- [ ] **Acceptance:** Clean installed consumers run the examples. Repeated
  expired keys retain their original identity, and changing APIs cannot bypass
  an existing uniqueness constraint or create a second intended effect.

## 9. Bulk submission

### FT-025: Specify bulk transaction and result behaviour

**Dependencies:** FT-022.

Define bounded bulk input, per-item identities, ordering of returned results,
and conflicts within a batch. Make all-or-nothing acceptance on one database
the initial contract. Document that returning task IDs inside an outer
transaction does not imply committed acceptance.

- [ ] **Acceptance:** Duplicate items, changed-intent conflicts, invalid
  arguments, and outer rollback have deterministic outcomes. Cross-database
  atomicity and larger Lambda execution batches are outside this feature.

### FT-026: Implement efficient, bounded bulk acceptance and dispatch

**Dependencies:** FT-023, FT-025.

Reduce repeated validation and database work while preserving each task's
deduplication and policy checks. Coalesce post-commit dispatch work where useful
and defer excess publication to reconciliation. Keep per-task dispatch budgets
and execution-attempt accounting intact.

- [ ] **Acceptance:** Failure rolls back an atomic batch, and uncertain or
  partial publication leaves all accepted records recoverable. Large batches
  do not cause unbounded synchronous publication in the producer process.

### FT-027: Benchmark and verify bulk behaviour

**Dependencies:** FT-026, FT-012.

Compare individual and bulk submission at documented batch sizes. Measure
query count, transaction duration, producer latency, and later execution.
Exercise duplicate batches and concurrent producers.

- [ ] **Acceptance:** Results identify the workload and tested limits. Tests
  account for every committed item and prove no duplicate logical acceptance
  under repeated batches. Installed examples preserve batch-one execution.

## 10. Pre-deployment compatibility reports

### FT-028: Produce a candidate application task manifest

**Dependencies:** None.

Describe the candidate image's importable task paths, payload versions, routes,
context contracts, workflow definition versions, and application/package
identities. Generate it from candidate configuration and code without running
task bodies or relying on a separate logical task-name registry.

- [ ] **Acceptance:** Reports identify the exact inspected artifact. Missing
  imports and unsupported configuration produce actionable findings, and
  manifest generation performs no task or business-state mutation.

### FT-029: Compare the candidate with accepted unfinished work

**Dependencies:** FT-028, FT-007; WF-003.

Inspect pending, running, and held tasks and waiting workflow definitions.
Identify paths, versions, destinations, or context contracts the candidate
cannot execute. Bound queries and record the snapshot boundary so the report
does not claim to cover later submissions automatically.

- [ ] **Acceptance:** An import shim is recognised, an unsupported saved queue
  is flagged, and waiting workflow steps are included. Inspection never
  retargets accepted work. Inaccessible evidence yields an incomplete report,
  rather than a clean compatibility result.

### FT-030: Integrate compatibility reports with deployment review

**Dependencies:** FT-029, FT-009.

Provide human-readable and JSON reports with stable exit behaviour. Add a CI
and deployment recipe that checks the candidate against the relevant backlog
and explains additive rollout and rollback requirements.

- [ ] **Acceptance:** A deployment scenario detects stranded old work before
  activation. A compatible replacement passes and executes that work. The
  retained report links candidate identity, inspected environment, snapshot,
  findings, and any explicitly unresolved compatibility obligations.

## 11. Provider cooldowns and shared rate limits

### FT-031: Define provider retry and quota policies

**Dependencies:** None.

Specify named provider scopes, bounded `Retry-After` handling, cooldowns, and
quota units. Distinguish task-start admission from limits on each external API
request. Define clock, outage, and ambiguous-response behaviour, including what
happens when a requested retry falls outside a task's usable window.

- [ ] **Acceptance:** Policy examples distinguish concurrency from request
  rate. Provider retry guidance cannot silently be shortened into an earlier
  call, and unavailable quota state has an explicit documented outcome.

### FT-032: Implement shared cooldown and permit coordination

**Dependencies:** FT-031.

Persist provider cooldowns and implement atomic shared permits for the declared
quota model. Denied task-start admission defers eligibility without starting
an attempt. Provide a request-level integration that respects the handler's
remaining budget and does not sleep indefinitely inside Lambda.

- [ ] **Acceptance:** Independent workers obey the shared admission model.
  Lost permits are handled conservatively, denied admission cannot bypass
  ownership, and the implementation states precisely which starts or requests
  it limits. A permit is not represented as proof of a provider-side outcome.

### FT-033: Verify limits and expose provider controls

**Dependencies:** FT-032, FT-009, FT-012.

Exercise quota boundaries, concurrent replicas, provider throttling, restarts,
and unavailable coordination state. Add provider inspection, audited controls,
and bounded telemetry. Document downstream integrations for tasks that make
several requests per attempt.

- [ ] **Acceptance:** Recorded tests establish the claimed quota behaviour and
  absence of attempt consumption on denied task-start admission. Operators can
  explain a cooldown without resetting task budgets or hiding an ambiguous
  external effect.

## 12. Tenant isolation and fair scheduling

### FT-034: Define trusted tenant context and access rules

**Dependencies:** FT-022.

Define how an application supplies authenticated tenant context for submission,
result lookup, inspection, recovery, and telemetry. The existing `tenant_id`
deduplication scope is not an authorisation boundary. Specify migration and
access rules for existing records without tenant ownership.

- [ ] **Acceptance:** The contract covers cross-tenant reads and mutations,
  staff roles, and sensitive details. Task arguments cannot establish trusted
  tenant identity by themselves, and legacy data does not become globally
  accessible by default.

### FT-035: Enforce tenant access and admission fairness

**Dependencies:** FT-034, FT-023.

Apply tenant scope consistently through public APIs, admin, commands, task
associations, and enabled workflows. Add configurable admission budgets and
fair discovery so one tenant cannot indefinitely consume shared capacity.
Preserve lease fencing, database ownership, and honest queue-order guarantees.

- [ ] **Acceptance:** Cross-tenant lookup and recovery are refused. Concurrent
  claims respect configured budgets, abandoned attempts release capacity
  through recovery, and a busy tenant does not starve an eligible smaller one.

### FT-036: Prove tenant isolation and publish operating guidance

**Dependencies:** FT-035, FT-012; WF-010.

Test API, browser, command, workflow, audit, and artifact integration boundaries
where enabled. Run mixed-tenant workloads and measure fairness and database
overhead. Document the application authentication responsibilities and the
extent of isolation supplied by the package.

- [ ] **Acceptance:** Negative access tests cover identifiers obtained from
  another tenant. Legacy migration is explicit, metrics avoid unbounded tenant
  dimensions, and measured fairness claims state their workload and limits.

## 13. Artifact-reference helpers

### FT-037: Define immutable object references and retention

**Dependencies:** None.

Specify bounded references containing object identity/version, size, checksum,
and ownership metadata. Keep temporary signed download URLs out of persisted
payloads. Define retention while tasks or workflow recovery still need an
object, and cleanup for uploads whose database transaction never commits.

- [ ] **Acceptance:** The contract covers missing, corrupt, expired, and
  orphaned objects. It does not claim atomic commitment across the database
  and object storage, and it preserves the original task's accepted reference.

### FT-038: Implement upload, retrieval, and reference tracking

**Dependencies:** FT-037.

Provide bounded streaming helpers, immutable object keys, integrity checking,
and controlled download access using configured storage permissions. Track
committed references and collect confirmed orphans after a documented grace
period. Avoid holding database transactions open across uploads.

- [ ] **Acceptance:** Retries reuse the accepted artifact identity. Rollback
  creates no executable task and leaves only a discoverable orphan eligible
  for later cleanup. Missing or invalid artifacts produce explicit task errors
  without overwriting accepted input references.

### FT-039: Integrate artifact inspection and verify cleanup safety

**Dependencies:** FT-038, FT-012; WF-006.

Add artifact-aware inspection and a document-processing example. Protect
objects needed by unfinished work and by the supported failed-step recovery
window. Test expired access URLs, credential rotation, checksum mismatch, and
cleanup races. Integrate tenant checks when tenant support is enabled.

- [ ] **Acceptance:** Cleanup cannot remove an artifact still needed for
  execution or supported recovery. Signed access respects authorisation and
  is omitted from logs. Installed and deployed examples recover from lost
  responses without creating conflicting artifact versions.

## 14. Broader Django compatibility

### FT-040: Evaluate additional Django versions and the task backport

**Dependencies:** None.

Compare the task APIs and migration requirements used by the package with
candidate Django versions. Start with Django 6.0; evaluate the `django-tasks`
backport for older applications only where there is a useful target audience.
Record runtime and dependency constraints before selecting a support matrix.

- [ ] **Acceptance:** A feasibility report identifies supported candidates,
  incompatible APIs, migration risks, and estimated maintenance scope.
  Existing version bounds stay unchanged until compatibility has evidence.

### FT-041: Implement the selected compatibility boundary

**Dependencies:** FT-040.

For feasible selected versions, isolate necessary API differences without
changing task identity or execution guarantees. Keep unsupported combinations
explicit and preserve the current Django path. Avoid silently dropping
features to satisfy imports.

- [ ] **Acceptance:** Selected versions pass backend contract, serialization,
  context, results, and migration checks. Unsupported combinations fail with
  a clear explanation. If no candidate is viable, record that decision and
  close the task without widening support claims.

### FT-042: Publish only the compatibility that passes candidate verification

**Dependencies:** FT-041, FT-012.

Run the selected Python/Django/dependency combinations from clean wheel and
source installations. Include relevant databases and schema upgrade fixtures.
Update dependency bounds, CI, and documentation from the passing evidence.

- [ ] **Acceptance:** Every newly advertised combination has recorded results
  for the candidate. Missing or skipped mandatory cases cannot expand the
  support statement. A decision to retain the current bounds is documented
  when expansion does not justify its compatibility cost.

## 15. External-effect recovery recipes

### FT-043: Specify repeatable and ambiguous provider scenarios

**Dependencies:** None.

Define examples for an idempotent external API, a receiver with no duplicate
protection, and a provider with outcome lookup. Explain submission identities,
business operation keys, replay, remote retention windows, and outcome
uncertainty. Keep provider-specific assumptions explicit.

- [ ] **Acceptance:** Each recipe states when retry is safe, when a task must
  hold, and which evidence an operator needs. No example claims exactly-once
  external effects solely because a task record or operation key exists.

### FT-044: Build sample integrations using package extension points

**Dependencies:** FT-043, FT-023.

Implement controlled sample provider/receiver integrations using operation-key
policies, bounded request timeouts, execution budgets, and ambiguity outcomes.
Show outcome lookup and an audited recovery path. Keep fault injection owned
by disposable test infrastructure and the sample.

- [ ] **Acceptance:** A provider can accept a request and lose its response.
  The sample then either retrieves/repeats the protected operation safely or
  produces a visible ambiguity hold. Replays preserve the intended effect
  identity and original attempt history.

### FT-045: Verify effect recovery and publish the recipes

**Dependencies:** FT-044, FT-012.

Run duplicate delivery, timeout after acceptance, lookup failure, expired
provider idempotency windows, and operator recovery scenarios. Package the
examples as application-owned code and explain how consumers adapt them to
their provider's guarantees.

- [ ] **Acceptance:** Supported protected retries produce one intended sample
  effect. Unsupported ambiguous repetition is held rather than automatically
  approved. Evidence distinguishes injected faults from actual service
  behaviour, and a clean consumer can reproduce the documented scenarios.
