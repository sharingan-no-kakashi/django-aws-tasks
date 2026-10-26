---
title: "Django Tasks on AWS: Implementation Backlog"
description: An ordered implementation backlog for the reusable Django Tasks backend, Lambda handlers, Terraform, sample application, developer tools, and release verification.
type: reference
status: seed
version: "0.1"
created: 2026-10-18
modified: 2026-10-18
tags:
  - django
  - python
  - aws
  - serverless
  - background-tasks
draft: true
---

> Historical snapshot. Checkboxes preserve the original plan; current work belongs in GitHub Issues.


# Django Tasks on AWS: Implementation Backlog

Current implementation/evidence status: [docs/acceptance-status.md](../../development/2026-10/acceptance-status.md).
The optional workflow extension has a separate [improvement backlog](todo-workflows.md).
Additional developer experience and product features have a separate
[feature backlog](todo-features.md).
The emulated AWS test tier has a separate [LocalStack backlog](todo-localstack.md).
Unchecked acceptance boxes require recorded acceptance evidence; implemented code
alone does not check off AWS, capacity, or release requirements.

Database support targets PostgreSQL, MySQL, MariaDB, and Oracle through the
shared adapter contract. PostgreSQL-specific examples below reflect the
original reference implementation. Shared coordination and E2E acceptance must
cover the supported engine matrix; a PostgreSQL-only result does not establish
MySQL, MariaDB, Oracle, or managed-RDS compatibility.

This is the implementation backlog for [[Django tasks design]]. The design defines the protocol; these tasks define the work and the evidence needed to deliver it. They cover the installable backend, consumer application, infrastructure, developer operations, and eventual release. All tasks start incomplete. Nothing here claims that the backend has already been built or deployed.

Implementation status (2026-10-18): the execution core and local consumer now
exist, and the code review corrections are being verified against PostgreSQL 16.
Acceptance checkboxes remain open until each item's complete evidence is mapped;
passing the core tests does not complete the infrastructure, operator, or release gates.

Implementation belongs in a dedicated backend repository with a uv workspace, rather than inside this blog. The working distribution name is `django-tasks-aws`, with Python namespace `django_tasks_aws`; task DT-001 must settle naming before public release. References to files below describe the proposed implementation repository.

## How to use this backlog

Each numbered task is a work item suitable for an issue. Its checkbox is a completion gate, not a suggestion to write tests later. Implement the behavior and its tests together. Split a task into smaller pull requests when necessary, preserving its acceptance criteria and dependencies.

Dependencies are mandatory prerequisites for completing a task. Scaffolding, documentation, and interface design can start earlier. Task order is a readable default, not a requirement to finish every item in one phase before starting the next. For example, infrastructure work can progress while the execution protocol is being tested, once its configuration contract is stable.

Every implementation task must meet these common conditions:

- Its documented behavior has automated tests at the appropriate layer, including the failure branches it introduces.
- Shared database coordination tests run on each supported engine and use independent connections or processes where concurrency matters; engine-specific tests remain explicitly scoped.
- New runtime behavior lives in the package; the sample contains application tasks and business logic.
- Public API, settings, migration, compatibility, and operator documentation change with the implementation.
- Relevant checks pass, with no skipped mandatory cases reported as successful coverage.
- The test traceability file records which design obligations the change satisfies and links to executable tests and CI evidence.

Use `docs/verification-matrix.yaml` for that traceability file. Give each design invariant, transition, worker response outcome, and row in both section 18 failure matrices a stable requirement ID. Record test IDs, test layer, fault mechanism, and evidence. A proposed test name without an implemented assertion is still incomplete.

## Milestones and completion gates

| Milestone                              | Tasks         | Gate                                                                                                          |
| -------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------- |
| M0: Reproducible foundation            | DT-001–DT-004 | A clean checkout installs the library and consuming sample and runs real-PostgreSQL tests in CI.              |
| M1: Durable execution core             | DT-005–DT-018 | Enqueue, dispatch, claim, finalization, and reconciliation satisfy the transaction and ownership tests.       |
| M2: Local consumer works               | DT-019–DT-021 | A developer runs two named workers and recovers missed publication using the installed package.               |
| M3: Terraform deployment works         | DT-022–DT-028 | The immutable sample artifact runs through both workers on infrastructure created by the delivered Terraform. |
| M4: Operators can diagnose and recover | DT-029–DT-037 | Admin and CLI share audited actions; retention, logs, and alerts work without row editing.                    |
| M5: Failure claims have evidence       | DT-038–DT-045 | The full failure matrix, real AWS scenarios, deployment tests, and measured capacity experiments pass.        |
| M6: Release candidate is reproducible  | DT-046–DT-049 | Security review, documentation, built artifacts, fresh installation, and release rehearsal are complete.      |
| M7: First release                      | DT-050        | A reviewed version is published and its installed artifact is verified; release evidence is retained.         |

M3 is the first deployed vertical slice, not the production-readiness gate. M4 and M5 remain required for the first release.

## Phase A: Contract, repository, and test foundation

### DT-001: Freeze the first-release contract and requirement IDs

**Dependencies:** None. **Specification:** Design sections 0–2, 18, and 20.

**Implement:** Create a versioned implementation specification from the design, an API/settings outline, and the initial verification matrix. Declare the tested Python, Django, PostgreSQL, and deployment support boundaries after checking their current compatibility. Choose the repository and package names and check distribution-name availability. Keep availability checks distinct from owning a PyPI project name.

Keep the initial execution contract narrow: synchronous importable tasks, standard queues, batch size one, database retries, fixed leases, two named workers, and explicit ambiguity handling. Record exclusions such as workflows, strict ordering, coroutine execution, and strict requests-per-second limiting.

- [ ] **Acceptance:** Every required deliverable and modeled failure has a stable requirement ID; public extension points and unsupported behaviors are written down; naming and compatibility decisions have recorded evidence. No requirement disappears simply because it is difficult to test.

### DT-002: Create the uv library and workspace structure

**Dependencies:** DT-001. **Specification:** Sections 14.1 and 14.7.

**Implement:** Create the dedicated repository, source-layout Django package, separate sample workspace member, test directories, infrastructure directories, and documentation. Add package metadata, license, build backend, runtime bounds, development groups, Ruff configuration, and committed `uv.lock`. Define the intended public backend and handler module paths without pretending that placeholder implementations are functional.

Configure the sample to depend on the package through the workspace during development. Keep application-specific modules outside the library distribution.

- [ ] **Acceptance:** `uv sync --locked` works from a clean checkout; the package imports; the sample initializes Django; `uv build --no-sources` creates a wheel and source distribution without runtime references to local workspace paths. CI inspects the initial artifact contents.

### DT-003: Establish CI, database-engine fixtures, and fault-test utilities

**Dependencies:** DT-002. **Specification:** Section 18.

**Implement:** Add unit, database integration/concurrency, artifact, sample, admin/command, Terraform, and AWS E2E suite boundaries. Configure pull-request checks and the declared compatibility matrix. Provide isolated real database instances for PostgreSQL, MySQL, MariaDB, and Oracle, with engine-aware independent connection/process helpers, bounded asynchronous polling, deterministic retry randomness, and injectable SQS transport failures. Keep database-time assertions tied to the selected engine's clock.

Define markers and CI jobs so an unavailable optional local service is distinguishable from a skipped release requirement. Establish machine-readable test reports and a traceability check that becomes stricter at release gates.

Add the emulated AWS integration tier described in [the LocalStack backlog](todo-localstack.md), using the supported real database-engine profiles and packaged Lambda workers. Keep its reports separate from the real-AWS acceptance and capacity evidence required by this design.

- [ ] **Acceptance:** CI demonstrates real commit, rollback, competing-connection, and clean-artifact fixtures for the claimed engine matrix; test failures surface reliably. A deliberately omitted mandatory requirement or missing engine result blocks the relevant release claim. SQLite is not used as evidence for supported-engine locking behavior.

### DT-004: Bootstrap the consuming sample and domain assertions

**Dependencies:** DT-002, DT-003. **Specification:** Sections 14.6 and 18.5.

**Implement:** Create the independent Django sample with settings, URLs, migrations, admin authentication, and simple order/effect models. Define an operation identity and database uniqueness rule for observing a protected business effect. Add fixtures for operator and read-only users and a documented local PostgreSQL setup.

Prepare importable sample task modules and scenarios for short work, documents, transient failure, and uncertain external effects. Functional backend integration arrives in DT-019; do not copy a temporary queue or lease implementation into the sample.

- [ ] **Acceptance:** The sample starts, migrates, authenticates admin users, and tests its business-effect uniqueness. Its dependency is the library package, and an automated boundary check or review confirms there is no duplicated coordination implementation.

## Phase B: Durable records and Django integration

### DT-005: Implement the task, attempt, and control-event schema

**Dependencies:** DT-001–DT-003. **Specification:** Sections 4–5.

**Implement:** Add `TaskRecord`, `TaskAttempt`, and `TaskControlEvent`, initial migrations, status/outcome types, conditional uniqueness, and discovery indexes. Include execution and publication tokens separately, both budgets, persisted route/backend, payload version/hash, operation and enqueue identities, replay links, bounded diagnostic fields, and invocation metadata.

Enforce the documented lease, reservation, hold, terminal timestamp, counter, and attempt-ordinal constraints at transaction boundaries. Choose deletion behavior that preserves audit/replay identity through supported retention.

- [ ] **Acceptance:** Migration and constraint tests run on PostgreSQL; invalid state combinations are rejected; duplicate attempt ordinals and enqueue keys fail safely. Fresh installation and the initial migration-state consistency check pass. Schema fixtures cover every legitimate state without disabling constraints.

### DT-006: Implement named worker configuration and Django system checks

**Dependencies:** DT-002, DT-005. **Specification:** Sections 2, 3.1, 6, and 14.5.

**Implement:** Define typed configuration for backend aliases and named worker profiles, including trusted queue destinations, source ARN, worker identity, deadline, lease, concurrency, retry/delivery limits, and task-policy restrictions. Implement Django checks for missing routes, invalid timing, incompatible capabilities, and inconsistent identities.

Support `short` and `documents` as sample profiles. Distinguish application defaults from the deployment manifest that resolves actual AWS resources. Never accept a destination queue URL from an untrusted task payload.

- [ ] **Acceptance:** Configuration tests reject unsafe lease/timeout combinations, duplicate or unknown routes, disallowed task destinations, and missing required settings. Producer, worker, and sweeper derive consistent route identities. Runtime context validation still detects drift after startup checks have passed.

### DT-007: Implement import-path identity and versioned JSON serialization

**Dependencies:** DT-002, DT-006. **Specification:** Section 6.

**Implement:** Persist the Django Task's fully qualified `module_path`, versioned JSON arguments, canonical identity hash, and bounded result format. Resolve with Django's import utilities and validate that the imported object is a supported Task in an allowed module. Reject unsupported callables and unsafe serialization formats.

Validate declaration shape at definition time, but perform the import round trip at enqueue time after module binding. Reconstruct per-record backend/queue options without mutating the imported task singleton. Define rename/re-export and payload-version compatibility behavior.

- [ ] **Acceptance:** Separate-process tests resolve a decorated task from its stored path, including two modules with the same short function name. Tests cover import cycles, missing/non-Task objects, unsupported arguments/results, canonical hashing, version rejection, and concurrent route overrides without shared-state mutation.

### DT-008: Implement retry, ambiguity, and task-policy decisions

**Dependencies:** DT-006, DT-007. **Specification:** Sections 8–10.

**Implement:** Define a small policy API keyed by import path for allowed routes, argument versions, maximum attempts, transient/permanent errors, safe repetition, operation identity, and uncertain outcomes. Implement bounded backoff with jitter and any supported `Retry-After` lower bound. Persist the policy values that must remain stable for accepted work.

Keep logical execution attempts, transport receives, SDK retries, and unclaimed dispatch reservations distinct. Treat unknown external outcomes according to an explicit handler policy rather than assuming every exception permits repetition.

- [ ] **Acceptance:** Decision-table tests cover each error/outcome class, final attempt, abandoned execution, ambiguity hold, unsupported version, delay bound, and policy validation. No policy relies on a Lambda error metric or SQS receive count as the logical attempt counter.

### DT-009: Implement transactional enqueue and submission deduplication

**Dependencies:** DT-005–DT-008. **Specification:** Sections 4.3 and 7.

**Implement:** Create the enqueue service that inserts durable pending work on the business transaction's database alias, freezes validated identity/arguments/route, and registers a bounded best-effort `on_commit()` dispatch callback. Returning from enqueue inside an outer transaction must not imply acceptance before commit.

Handle same-key races using database uniqueness and a safe conflict/savepoint path. Identical submissions return the original identity; changed payload, version, backend, or destination conflicts. Preserve business operation identity separately from submission deduplication.

- [ ] **Acceptance:** Real transaction tests cover outer rollback, nested savepoint rollback, callback failure, concurrent identical submissions, conflicting intent, and aliases. No rolled-back task can execute. A previously accepted key with expired details produces the documented error and retained ID without creating replacement work.

### DT-010: Implement the Django Tasks backend and result contract

**Dependencies:** DT-009. **Specification:** Sections 1, 5–7, and 16.4.

**Implement:** Expose the supported Django Tasks backend methods and capability flags, mapping task/result states and exceptions deliberately. Support decorator defaults and `.using(queue_name=...).enqueue(...)`, backend alias selection, supported scheduling options, and retained result lookup. Clearly distinguish scheduled, held, running, and terminal work in backend-specific diagnostics while honoring Django's public contract.

Add consumer-facing tests through the public Django Tasks API rather than testing only internal services.

- [ ] **Acceptance:** The sample enqueues and reads results through the configured backend. Tests prove producer route overrides are immutable per submission, unsupported features fail explicitly, transaction semantics remain intact, and result-expiry behavior is stable. Advertised capabilities match implemented behavior across the support matrix.

## Phase C: Publication, execution, and recovery

### DT-011: Implement token-owned SQS publication reservations

**Dependencies:** DT-005, DT-006, DT-009. **Specification:** Section 7.

**Implement:** Reserve eligible dispatches in short transactions, consume the durable unclaimed-dispatch budget, and publish a versioned task-ID envelope outside database locks. Record confirmed publication or retry timing only while the original reservation still owns the eligible row. Bound SDK retries/timeouts; make uncertain send outcomes safe to repeat.

Implement per-entry result handling if using `SendMessageBatch`. Use the same publisher from callbacks, commands, and reconciliation. Diagnostic `last_dispatched_at` must not become a permanent exclusion from rediscovery.

- [ ] **Acceptance:** Tests cover competing publishers, death after reservation, accepted send with lost response, failed metadata commit, partial batch failure, reservation expiry, and a delayed publisher response after claim/retry/hold. Stale tokens cannot overwrite newer eligibility, and no SQS call occurs while coordination locks are held.

### DT-012: Implement atomic execution claims and fixed leases

**Dependencies:** DT-005, DT-006, DT-008. **Specification:** Sections 8–9.

**Implement:** Atomically claim due, unheld pending work for the actual worker/source identity. In one committed transaction allocate the attempt token/ordinal, increment the execution counter, set a lease, create attempt metadata, invalidate publication ownership, and reset the unclaimed-dispatch counter.

Use database time. Verify that the lease covers the invocation's actual remaining lifetime plus margin. Return the validated claimed record. Do not execute until the claim commit is known; an ambiguous commit must not authorize speculative execution.

- [ ] **Acceptance:** Independent connections/processes prove one current claim, no attempt increments for duplicates, and rejection of future/held/terminal/wrong-route work. Tests cover near-deadline invocations, exhausted budgets, ambiguous commits, and death immediately after a committed claim.

### DT-013: Implement ownership-checked completion and retry transitions

**Dependencies:** DT-008, DT-012. **Specification:** Sections 8–10.

**Implement:** Add success, handled-failure, retry, terminal-failure, and uncertainty transitions that lock the task before its attempt and recheck current token, status, and database-clock expiry. Validate and bound results/errors before storing them. Close attempt history and clear the lease atomically with the task transition.

Keep business execution outside the coordination transaction. Never finalize through an unconditional model save. Preserve the chosen route and operation identity when scheduling retries.

- [ ] **Acceptance:** Tests prove obsolete or expired attempts cannot finish current work, concurrent finalization is safe, counters remain durable, and final-attempt failures terminate correctly. Commit failure/uncertainty propagates as a transport-level failure until the durable outcome can be established; it is not acknowledged optimistically.

### DT-014: Implement the task execution wrapper and effect-safety contract

**Dependencies:** DT-007, DT-008, DT-012, DT-013. **Specification:** Sections 6 and 9–10.

**Implement:** Invoke the claimed imported Task with the documented task context, backend/route reconstruction, application arguments, and operation identity available through a supported API. Classify handler outcomes through policy and finalize through DT-013. Reserve a finalization allowance and reject unsupported execution modes explicitly.

Document what an idempotent handler must do for local database effects and external providers. Provide explicit uncertainty reporting; a local "sent" flag alone must not be advertised as protection against an uncertain remote call.

- [ ] **Acceptance:** Tests demonstrate a protected local effect across repeated attempts, a simulated provider operation key across retries, and an unprotected ambiguous effect moving to hold. Handler import errors, invalid results, permanent errors, and deadline exhaustion reach the specified durable outcomes.

### DT-015: Ship the package-owned SQS Lambda worker handler

**Dependencies:** DT-010–DT-014. **Specification:** Sections 9, 12, and 14.1.

**Implement:** Expose `django_tasks_aws.handlers.worker.lambda_handler`. Initialize Django from consumer configuration, parse envelopes, validate source identity, and apply the complete worker response decision table. Return partial-batch failures using SQS message IDs. Batch size one is the supported deployment baseline; additional records must receive deliberate bounded handling if encountered.

Handle already terminal, live-running, held, early, expired, missing, malformed, incompatible, and wrong-route cases distinctly. Database-owned retries are acknowledged only after their scheduling commit.

- [ ] **Acceptance:** Event-contract tests cover every response-table row and mixed outcomes. Duplicate notifications do not execute business code. Malformed or misrouted deliveries remain eligible for transport failure/DLQ handling, while committed compatibility holds stay inspectable. The sample imports the public handler rather than wrapping a copied protocol.

### DT-016: Implement Lambda lifecycle and database connection management

**Dependencies:** DT-015. **Specification:** Sections 14.1–14.3.

**Implement:** Make cold/warm initialization, configured identity, connection cleanup, and invocation boundaries explicit. Use the writer endpoint for coordination. Bound statement/lock waits and release or recycle connections deliberately after normal and exceptional invocations. Ensure startup failures do not fabricate attempts or delete unfinished work.

Apply the same lifecycle helper to the sweeper entry point when it is added. Keep migrations out of worker cold starts and avoid requiring an unmeasured connection proxy.

- [ ] **Acceptance:** Repeated warm-invocation tests and broken-connection tests recover correctly without open coordination transactions or monotonic connection leakage. Failure before claim leaves zero attempts. Tests verify settings isolation, writer routing, cleanup on error, and safe behavior when initialization or cleanup itself fails.

### DT-017: Implement expired-attempt recovery and due-work reconciliation

**Dependencies:** DT-011–DT-014. **Specification:** Section 11.

**Implement:** Recover expired leases in bounded batches with skip-locked selection and ownership/time rechecks. Mark abandoned attempts with unknown outcomes, schedule safe retries, hold ambiguous work, or terminate exhausted work. Discover all eligible pending tasks for publication, including previously dispatched rows. Hold work that exhausts its durable unclaimed-dispatch budget.

Use the shared publisher, preserve routes, respect route pauses and holds, and make overlapping runs safe. Detect and report impossible invariants without guessing a repair.

- [ ] **Acceptance:** PostgreSQL race tests cover live leases, finalizer/reaper competition, overlapping sweepers, failed sends, paused routes, exhausted budgets, and large backlogs. Every run is bounded, makes observable progress when possible, and cannot revive terminal or held work automatically.

### DT-018: Ship the scheduled sweeper and reconciliation health reporting

**Dependencies:** DT-006, DT-016, DT-017. **Specification:** Sections 11 and 15.

**Implement:** Expose `django_tasks_aws.handlers.sweeper.lambda_handler` using the shared lifecycle and reconciliation services. Add per-route fair scheduling, a bounded run budget, summary output, and a health heartbeat even when idle. Report discovery, dispatch, recovery, hold, invariant-error, and oldest-due metrics separately.

Define what happens when one route or database operation fails mid-run, and preserve work for subsequent runs. Monitoring must distinguish "scheduler invoked" from "reconciliation progressed."

- [ ] **Acceptance:** Tests prove idle heartbeat emission, partial-run failure reporting, fairness under one noisy route, overlap safety, and recovery after a missed invocation. Running the packaged handler with immediate enqueue publication disabled eventually discovers the committed sample task without reading queue contents.

## Phase D: Complete the local consuming application

### DT-019: Implement the sample task scenarios through public APIs

**Dependencies:** DT-004, DT-010, DT-014, DT-017. **Specification:** Section 14.6.

**Implement:** Complete the sample's short idempotent task, route-restricted document task, transient failure, and ambiguous-effect scenario. Add business services that enqueue within real transactions and record effects by operation key. Allow the same permitted task to target either named worker at submission time.

Keep test fault behavior explicit in sample tasks or controlled test adapters. The package must not gain an unauthenticated production bypass for forcing outcomes or ownership changes.

- [ ] **Acceptance:** Sample integration tests exercise commit/rollback, both routes, retry, ambiguity hold, and reconciliation using public interfaces. They assert business effects and persisted worker identities, not merely a successful return from enqueue. No sample module duplicates claiming, lease, retry, or recovery code.

### DT-020: Provide local worker and sweep management commands

**Dependencies:** DT-015, DT-017, DT-019. **Specification:** Sections 14.6 and 16.7.

**Implement:** Ship package commands `tasks_worker` and `tasks_sweep` that call the same execution/reconciliation services as Lambda. Define a bounded local worker mode, route selection, stop behavior, output, errors, and any local transport configuration. Provide a fresh-checkout development recipe with local PostgreSQL and a documented queue setup.

Local execution must still claim and finalize through the production protocol. It must not silently call a task body without coordination when a developer expects worker behavior.

- [ ] **Acceptance:** Command integration tests submit and process work on both routes, recover lost publication, handle interruption, and enforce lease/route policy. A developer can run the documented sequence without AWS deployment or copied Python snippets. Local emulation is clearly distinguished from real AWS verification.

### DT-021: Build and verify the installed sample and Lambda image

**Dependencies:** DT-002, DT-016, DT-018–DT-020. **Specification:** Sections 14.1, 14.6–14.7.

**Implement:** Build the backend wheel, install it into an isolated sample environment outside the checkout with editable sources disabled, and build one consumer Lambda image containing the sample task modules and pinned backend. Configure worker and sweeper commands to use the package's public entry points. Record application/package revisions and image digest.

Add an artifact smoke suite that runs migrations, resolves stored task paths in a separate process, and invokes public handlers with test events.

- [ ] **Acceptance:** The installed sample works without repository source paths or workspace resolution. The image supports both worker profiles and the sweeper without copied handlers, contains no credentials, and produces a digest used by deployment. Rebuilding from the source distribution also yields a usable backend installation.

## Phase E: Terraform and the first real AWS execution

### DT-022: Define the Terraform module and shared route manifest

**Dependencies:** DT-006, DT-018. **Specification:** Section 14.5.

**Implement:** Define the reusable module's typed worker map, shared environment, immutable image digest, existing VPC/database/secret references, tags, and outputs. Specify a machine-readable route manifest consumed by producers, workers, and the sweeper. Separate shared route configuration from worker-local expected identity.

Add validations/preconditions for supported batch settings, timing relationships, profile identity, and required inputs. Document which prerequisites the module integrates with and which the sample bootstrap provisions.

- [ ] **Acceptance:** Module interface tests validate two distinct profiles and reject unsafe or incomplete configurations. The output manifest loads through the Python configuration code and agrees on route, source ARN, worker identity, and timing. Outputs expose references rather than secret values; no mutable image tag is accepted as release identity.

### DT-023: Provision per-worker SQS, DLQ, Lambda, and event mappings

**Dependencies:** DT-022. **Specification:** Sections 12–14.5.

**Implement:** For each named profile create a standard source queue, DLQ/redrive policy, Lambda worker, log group, and SQS event source mapping with partial-batch responses enabled. Configure batch size one/window zero, independent concurrency/resource limits, source visibility, and retention. Apply the immutable consumer image with the package worker entry point.

Keep logical task failure independent of transport redrive. DLQ entries remain delivery envelopes, not authoritative failed-task records.

- [ ] **Acceptance:** Terraform plan tests prove one isolated resource set per profile, correct source bindings, partial-response configuration, retention/redrive settings, and concurrency bounds. Unsafe visibility/deadline values fail validation. No FIFO behavior or ordering guarantee is introduced implicitly.

### DT-024: Implement IAM, secrets, and network integration

**Dependencies:** DT-022. **Specification:** Sections 14.2–14.3.

**Implement:** Define separate producer, worker, sweeper, and Scheduler permissions, plus scoped test-runner access. Configure database security groups, secret references, encryption permissions where used, and required service/provider egress. Distinguish AWS-managed SQS polling from SDK sends performed by producers and sweepers.

The reusable module integrates with existing networking; the sample root explicitly supplies required routes/endpoints/egress. Keep business-service permissions extensible through documented consumer inputs and validate configuration without exposing credentials.

- [ ] **Acceptance:** Plan/policy tests prove roles are scoped to intended resources and no unnecessary administrator grants exist. Deployed checks later prove writer-database, secret, publisher-to-SQS, and sample-provider connectivity. The network diagram and cost inventory include every newly provisioned egress/proxy/endpoint resource.

### DT-025: Provision scheduled recovery and infrastructure health signals

**Dependencies:** DT-018, DT-023, DT-024. **Specification:** Sections 11, 14.5, and 15.

**Implement:** Add the sweeper Lambda using the same consumer image, EventBridge Scheduler schedule, invocation role, bounded delivery retries, and schedule-delivery failure destination. Provision finite log retention and baseline alarms for failed scheduling, worker errors/throttling, source age, and source/scheduler DLQs.

Define the metric names and dimensions expected from application health reporting. Full progress/hold dashboards and alert verification follow in DT-037; a successful scheduler invocation alone must not be the recovery-health signal.

- [ ] **Acceptance:** Terraform tests verify handler/image identity, schedule target and role, delivery-failure configuration, log retention, and alarm resources. Disabled or broken delivery is observable. Schedule configuration is documented as periodic reconciliation rather than an exact execution-time guarantee.

### DT-026: Deliver the complete sample bootstrap and deployment workflow

**Dependencies:** DT-021, DT-023–DT-025. **Specification:** Sections 14.4–14.6 and 18.5.

**Implement:** Provide the sample Terraform roots and scripts for bootstrap resources, image build/push, digest-based task-system deployment, explicit migrations, configuration delivery, and teardown. Provision or explicitly integrate PostgreSQL, networking, database secrets, and the image repository. Keep image/package builds outside Terraform provisioners.

Provide restricted sample producer and read-only inspection entry points if CI cannot reach the private database directly. Include a documented authenticated path for the sample's admin/browser workflows; do not assume a private database is directly reachable from a developer laptop.

- [ ] **Acceptance:** A scripted fresh environment reaches a migrated, configurable sample deployment without console-created task resources. Outputs configure all consumers consistently. Deployment and teardown are repeatable, secrets stay out of ordinary outputs, and failed runs leave discoverable resources tagged by run ID.

### DT-027: Add Terraform validation, plan, and deployment tests

**Dependencies:** DT-023–DT-026. **Specification:** Sections 14.5 and 18.4.

**Implement:** Add formatting, initialization/validation, module plan assertions, policy/configuration checks, and isolated deployment tests. Test both worker profiles, route-manifest round trips, invalid timing, missing prerequisites, unsupported batch profiles, IAM boundaries, and nonsecret outputs. Pin supported provider/tool constraints and record actual versions in evidence.

Ensure the sample root consumes the public module interface rather than a separate set of manually duplicated task resources.

- [ ] **Acceptance:** Pull requests run the nondeployment checks. An isolated AWS job applies and destroys the delivered example successfully, checks generated identities against the manifest, and reports cleanup failures. Drift or invalid profile values produce an actionable failure before task execution.

### DT-028: Automate the first deployed end-to-end happy path

**Dependencies:** DT-019, DT-026, DT-027. **Specification:** Section 18.5.

**Implement:** Build the wheel and immutable consumer image, deploy through the sample Terraform, invoke the actual Django producer inside a transaction, and observe database task/attempt state plus the unique business effect. Submit a permitted task explicitly to each named worker and verify the recorded function/profile identity.

Use unique run identifiers, bounded polling, evidence collection, and cleanup on success or failure. Disable immediate publication for a separate case and wait for the deployed scheduled sweeper.

- [ ] **Acceptance:** CI proves real SQS-to-Lambda execution for both routes, successful stored results, rollback producing no work, and Scheduler-driven recovery of missed publication. Evidence records package/application/infrastructure versions and actual deployed identities. Mocks or local queue emulation do not satisfy this gate.

## Phase F: Developer and operator experience

### DT-029: Implement audited, idempotent control services

**Dependencies:** DT-005, DT-009, DT-011, DT-013, DT-017. **Specification:** Sections 4.4, 5, and 16.1–16.3.

**Implement:** Build shared services for inspecting action eligibility, releasing holds, creating linked replays, requesting eligible dispatch, and recovering expired work. Require actor, origin, reason, target scope, and request identity for mutations. Commit replay/release and their audit records atomically; distinguish a recorded dispatch request from a confirmed network outcome.

Replay creates a new task, retains the original terminal record, and preserves the operation key for the same intended effect. A new intended effect requires explicit policy and a new business identity. Never clear a live lease or overwrite executable arguments.

- [ ] **Acceptance:** Concurrent repeat requests produce one mutation/replacement; changed-intent reuse conflicts; a repeated release cannot release a later unrelated hold. Race tests cover workers and sweepers. Per-target bulk outcomes and durable audit remain correct when publication fails after commit.

### DT-030: Implement inspection queries and problem categorization

**Dependencies:** DT-005, DT-006, DT-017, DT-029. **Specification:** Sections 15–16.

**Implement:** Add shared bounded query services for task detail, attempts, audit, replay lineage, route summaries, and problem lists. Distinguish scheduled, due, dispatch-reserved, live-running, expired, retrying, held, and terminal states. Report eligibility and blocking reason using the same rules as control services.

Support filters, pagination, stable ordering, task/import-path/operation-key search, and safe diagnostic fields. Avoid loading full payloads or one related query per row for ordinary list views.

- [ ] **Acceptance:** Query tests cover each category, boundary timestamp, permissions integration, search, pagination, and mixed-route backlogs. Query-count checks and representative PostgreSQL plans show bounded inspection as terminal history grows. UI and commands can consume the same diagnostic model.

### DT-031: Ship read-only inspection and validation commands

**Dependencies:** DT-006, DT-010, DT-030. **Specification:** Section 16.7.

**Implement:** Add `tasks_list`, `tasks_show TASK_ID`, `tasks_check`, and `tasks_workers`. Define documented options, filters, pagination, configuration checks, JSON schemas, diagnostics on stderr, and stable exit behavior. Show attempt/error summaries, route configuration, current recovery blockers, and applicable log references.

Keep inspection commands read-only, including checks: they must not silently publish messages, reclaim leases, or rewrite invalid rows while diagnosing them.

- [ ] **Acceptance:** Installed-package command tests cover healthy/problem work, absent IDs, invalid input/configuration, permissions where applicable, pagination, JSON parsing, and stdout/stderr separation. State and queue spies prove read-only commands do not mutate tasks or call SQS publication.

### DT-032: Ship recovery commands and consistent dry-run behavior

**Dependencies:** DT-020, DT-029, DT-031. **Specification:** Section 16.7.

**Implement:** Add `tasks_dispatch TASK_ID`, `tasks_release TASK_ID`, and `tasks_replay TASK_ID`; complete operator options for `tasks_sweep`. Call shared services with reason, actor, request ID, route scope, bounded batch limits, and per-target results. Define which commands support `--dry-run` and make preview strictly free of state changes and queue calls.

Expose clear refusals for live leases, terminal redispatch, incompatible replay intent, and unauthorized policy changes. Do not offer a generic status setter or attempt-counter reset.

- [ ] **Acceptance:** CLI tests cover successful and ineligible actions, repeat/conflicting requests, dry-run snapshots, partial batch failure, JSON output, and exit status. The installed sample recovers held work and replays a terminal task with correct audit, lineage, and operation identity.

### DT-033: Ship packaged admin inspection and problem-task pages

**Dependencies:** DT-030, DT-031. **Specification:** Sections 16.5 and 16.8.

**Implement:** Register task, attempt, and audit models and add a package-owned failed/problem-task view. Provide list filters, search, pagination, state/hold reasons, attempt timeline, replay lineage, route summary, bounded error details, and CloudWatch link slots. Keep protocol fields and audit history read-only.

Define view permissions for read-only staff and restrict sensitive payload/diagnostic visibility appropriately. Package templates/assets through the normal Django app mechanism with no sample-specific admin code.

- [ ] **Acceptance:** Admin tests cover all problem categories, read-only roles, unauthorized access, related history, query counts, escaping, and missing-log metadata. The installed wheel renders its templates/assets in the sample, and users cannot bypass the protocol through model add/change/delete forms.

### DT-034: Add reviewed admin recovery actions and permission enforcement

**Dependencies:** DT-029, DT-032, DT-033. **Specification:** Sections 16.5–16.8.

**Implement:** Provide POST/CSRF-protected actions for eligible dispatch, hold release, expired recovery, and terminal replay. Use dedicated permissions, a bounded selection preview, reason/request identity, per-target eligibility, and per-target outcomes. Offer recovery links directly from problem-task details.

Make confirmation pages concrete: selected tasks, intended effect, preserved operation identity, route, and expected replacement/transition. State can change after preview, so services must revalidate at execution. Do not expose force-unlock for live work.

- [ ] **Acceptance:** Request and browser tests prove permissions, CSRF, preview-versus-execution races, double submission, partial selection failure, and audit correctness. Admin and CLI produce equivalent transitions and conflicts through the same services. A read-only staff user can diagnose permitted work without gaining mutation access.

### DT-035: Implement bounded retention without erasing recovery identity

**Dependencies:** DT-005, DT-010, DT-029, DT-030. **Specification:** Sections 4.3 and 16.4.

**Implement:** Add a documented retention service/command and configuration for detailed results, payload/error redaction, compact terminal identity, attempts, audit, and log retention alignment. Preserve deduplication and replay references for the promised window. Never delete unfinished tasks through age-based cleanup.

Implement bounded batches, dry-run, resumability, and state rechecks. Define how late SQS notifications distinguish retained terminal identity from a genuinely missing task, and what an old enqueue key returns after detailed results expire.

- [ ] **Acceptance:** Tests cover retention boundaries, concurrent completion/replay, legal late duplicates, audit/replay links, expired-result lookup, repeated cleanup, and protection of pending/running/held work. Cleanup cannot reopen tasks or convert a repeated submission into another business effect.

### DT-036: Implement structured telemetry and CloudWatch navigation

**Dependencies:** DT-015, DT-018, DT-029, DT-030. **Specification:** Section 15.

**Implement:** Add structured event logging across enqueue, publication, claim, completion, retry, hold, recovery, and control actions. Persist per-attempt request/region/log-group/log-stream metadata, generate validated CloudWatch links at display time, and provide task-correlated query guidance across attempts. Do not store one overwritten log URL on the task as its complete history.

Emit bounded-cardinality metrics for outcomes, publisher failures, lease expiry, holds, due age, and reconciliation health. Sanitize/truncate exceptions and keep raw task arguments and secrets out of default logs.

- [ ] **Acceptance:** Tests assert correlation fields, redaction, size limits, dimension bounds, and link construction. Deployed evidence navigates from a failed attempt to its actual invocation logs. Handled business failure is measurable even when Lambda reports a successful invocation.

### DT-037: Wire dashboards, recovery alerts, and route pause procedures

**Dependencies:** DT-025, DT-035, DT-036. **Specification:** Sections 11 and 15–17.

**Implement:** Complete Terraform dashboards and alarms for missing sweeper heartbeat, lack of progress/oldest due work, expired leases, new/aging holds, DLQs, source age, throttling, database connection pressure, and application failure outcomes. Separate held age from runnable delay and empty-system health from stuck-system health.

Document and test route pause/resume through trusted configuration and the shared selection rules. Specify alert ownership and actionable runbooks, including the distinction between transport redrive and logical replay.

- [ ] **Acceptance:** Deployed fault scenarios trigger the intended health signals and recovery clears them. A stopped sweeper, broken worker initialization, and held backlog remain visible. Pausing one route prevents new eligible claims/dispatch as specified without stealing live leases or blocking healthy routes.

## Phase G: Failure coverage and operational evidence

### DT-038: Complete the cross-boundary failure harness and coverage matrix

**Dependencies:** DT-018, DT-021, DT-028, DT-029. **Specification:** Sections 18.1–18.2.

**Implement:** Extend the feature-level tests with reusable controlled failures at claim/commit, send/response, effect/finalization, and reconciliation boundaries. Add sample-only fault tasks or a restricted test adapter for deployed cases. Document which faults are injected and which exercise actual AWS behavior.

Complete traceability for both design failure matrices and every worker response outcome. Assert task/attempt/audit state, effect identity, counters, ownership, and acknowledgment decisions; successful eventual completion alone is insufficient.

- [ ] **Acceptance:** Automated validation finds no unassigned requirement. Unit/integration cases reproduce lost callbacks, stale publishers, ambiguous commits, duplicates, and exhausted persistent budgets. Deployed cases are assigned to DT-041–DT-044, with missing executions still blocking release. Fault controls cannot be enabled through untrusted production task data.

### DT-039: Exercise generated state transitions and concurrency interleavings

**Dependencies:** DT-011–DT-018, DT-029, DT-035, DT-038. **Specification:** Sections 1.2, 5, and 18.4.

**Implement:** Build a model-based/state-machine test harness for enqueue, reserve, publish, claim, time advancement, finalization, recovery, holds, release, replay, and retention. Compare observable database state with the supported transition model. Add targeted multiprocess stress tests for finalizer/reaper, publisher/claim, and control-action races.

Retain random seeds and minimize failing sequences. Keep these tests bounded enough for their CI tier and avoid relying on arbitrary sleeps to arrange races.

- [ ] **Acceptance:** Runs assert all documented invariants after each committed transition, reject stale ownership changes, and reproduce failures from recorded seeds. Stress tests show no double current claim, counter corruption, unsafe revival, or lost audit. Coverage complements, rather than replaces, explicit failure-case assertions.

### DT-040: Verify installed artifacts, supported upgrades, admin, and CLI

**Dependencies:** DT-021, DT-031–DT-036. **Specification:** Sections 14.7, 16.8, and 18.4.

**Implement:** Expand clean wheel and source-distribution jobs to cover migrations, public entry points, backend API, templates/assets, commands, and browser workflows. Exercise declared dependency bounds and supported Python/Django versions beyond a single lockfile. Test fresh installation and migration from every declared supported previous schema fixture/release.

Run the sample outside the checkout and use a browser to diagnose failed work, release a hold, and inspect replay lineage. Keep first-release schema fixtures explicit until prior published releases exist.

- [ ] **Acceptance:** The installed package passes the supported matrix and sample workflows with editable sources disabled. Missing packaged files, undeclared runtime dependencies, broken upgrade paths, unauthorized actions, invalid JSON output, or changed-intent replay all fail CI. Required cases cannot be satisfied by testing source imports alone.

### DT-041: Verify AWS delivery, routing, and acknowledgment boundaries

**Dependencies:** DT-028, DT-036, DT-038. **Specification:** Sections 7, 9, 12, and 18.5.

**Implement:** Add real AWS E2E cases for duplicate envelopes, concurrent invocations, both producer-selected routes, retry/sweeper route preservation, missing fast publication, future eligibility, and completion followed by lost acknowledgment. Exercise actual partial-batch failure behavior in a dedicated test mapping if the handler supports multi-record input; retain batch one as the release baseline.

Include malformed envelopes, unknown versions, wrong source/profile, and missing task identity. Assert expected durable state and transport behavior separately.

- [ ] **Acceptance:** Both worker profiles pass the routing and concurrency assertions; duplicates cannot claim current/terminal/held work or consume extra attempts. Tests prove failed-item identifiers correspond to SQS records and observe redelivery where required. Every delivery/routing matrix row has retained deployed evidence or its designated lower-layer proof.

### DT-042: Verify real timeouts, abandoned leases, and uncertain effects

**Dependencies:** DT-028, DT-034, DT-038, DT-041. **Specification:** Sections 8–11 and 19.2.

**Implement:** Force actual Lambda hard timeout and a controlled out-of-memory termination in isolated sample scenarios. Cover death immediately after claim and effect success before completion persistence. Observe that recovery waits for lease expiry, records an abandoned unknown outcome, and applies safe-retry or ambiguity-hold policy.

Test the stale-attempt finalization boundary with a controlled harness, and demonstrate protected operation identity across repeated attempts and manual replay. Distinguish the lease from SQS visibility in assertions.

- [ ] **Acceptance:** No exception handler or finally block is required for progress after abrupt termination. Live leases remain untouched; safe repetitions preserve one intended business effect; unsafe uncertain effects hold visibly. Real service termination and injected transaction faults are labeled separately in the evidence.

### DT-043: Verify DLQ, delivery holds, and operator recovery on AWS

**Dependencies:** DT-034, DT-037, DT-038, DT-041. **Specification:** Sections 11, 13, and 16.

**Implement:** Break worker initialization, exhaust transport receives and durable unclaimed-dispatch reservations, and verify DLQ alarms plus a visible delivery hold. Include a stale envelope for a task that has already succeeded. Repair the deployment, inspect via commands/admin, release the eligible hold, and verify progress on its original route.

Exercise terminal replay through both control surfaces with repeat request IDs and conflicting intent. Demonstrate that redriving an envelope and creating new logical work have distinct effects.

- [ ] **Acceptance:** Initialization failures leave zero execution attempts until a claim occurs; budgets survive new SQS messages; no automatic replay follows DLQ arrival. Authorized operator recovery is audited/idempotent, preserves business identity, and cannot restart a live or successful task accidentally. Browser and CLI evidence is retained.

### DT-044: Verify compatible deployments, database failures, and outages

**Dependencies:** DT-027, DT-035, DT-038, DT-041–DT-043. **Specification:** Sections 11, 14.2–14.4, and 18.3.

**Implement:** Automate additive migration/worker/producer rollout with an old-payload backlog, task-path compatibility shim, unsupported-version hold, and compatible rollback. Break connections and exercise actual database failover for the supported deployment profile using isolated infrastructure. Test a stopped sweeper, stopped worker route, and backlog recovery beyond normal redispatch intervals.

Record how operators pause affected routes and restore progress. Include the separate warning/runbook for database restore relative to already completed external effects; a restore must not blindly resume all work.

- [ ] **Acceptance:** Old accepted work survives compatible rollout/rollback; coordination never uses a lagging reader; ambiguous commits do not authorize unverified execution. Failover/outage evidence records actual service events, resulting attempts/holds, and recovery time. A killed connection alone is not presented as a genuine service failover test.

### DT-045: Measure capacity, connection budgets, and actual cost inputs

**Dependencies:** DT-037, DT-041–DT-044. **Specification:** Sections 2, 17, and 18.3.

**Implement:** Run the documented task mix, sustained workload, and five-minute burst at 500 submissions per minute with several concurrency settings. Measure start/completion latency percentiles, oldest due work, queue age, retry/abandonment/hold rates, database sessions/waits, and reconciliation progress. Exercise both profiles and the missed-publication path under load.

Record usage-based costs with region, runtime, memory, architecture, networking, database, storage, logs, retries, and free-tier assumptions stated. Adjust defaults only with evidence and rerun affected checks.

- [ ] **Acceptance:** A reproducible report establishes a supported workload envelope, connection headroom, outage recovery behavior, and cost inputs. Capacity caps are not described as strict rate limits. Part 3 can cite actual measurements without turning an illustrative estimate into an operational guarantee.

## Phase H: Documentation, release candidate, and publication

### DT-046: Review security, configuration, and operational failure boundaries

**Dependencies:** DT-024, DT-034–DT-037, DT-043, DT-044. **Specification:** Sections 6, 14–16.

**Implement:** Review import allowlists, payload/result limits, exception redaction, secret handling, IAM/network scope, admin permissions, CSRF, action idempotency, and diagnostic retention. Inspect the image and distribution for credentials, unintended sample code, and unsafe defaults. Verify that test-only fault controls are absent or unenableable in normal deployments.

Review backup/restore, pause/resume, missing-sweeper, unknown-effect, DLQ, and database-exhaustion procedures against the observed failure behavior. Resolve findings through focused changes with regression tests.

- [ ] **Acceptance:** The review produces a retained findings/resolution record with no unresolved release-blocking defect. A fresh configuration fails safely when unsafe or incomplete, sensitive details are permissioned/redacted, and operator actions cannot bypass current lease ownership.

### DT-047: Write and validate the complete developer journey

**Dependencies:** DT-020, DT-026, DT-032–DT-037, DT-040, DT-045, DT-046. **Specification:** Sections 14.6–14.7, 16, and 20.

**Implement:** Finish package README, installation/configuration guide, task authoring and idempotency guidance, routing examples, local development, admin/CLI operations, Terraform deployment, compatibility upgrades, failure runbooks, test execution, and teardown. Document public extension points and the difference between an import path, enqueue key, and business operation key.

Run the documented journey from a clean environment, using both editable development and installed-artifact modes. Prepare the implementation evidence needed by [[Django tasks part 3]] without making unmeasured claims.

- [ ] **Acceptance:** Another developer can install, migrate, enqueue to both workers, diagnose/recover a failure, run verification, deploy, and clean up using the delivered instructions. Documented commands are exercised automatically where practical; remaining manual steps have explicit expected outputs and prerequisites.

### DT-048: Implement gated versioning and release workflows

**Dependencies:** DT-002, DT-027, DT-038, DT-040. **Specification:** Sections 14.7 and 18.4.

**Implement:** Add version/changelog conventions and CI that builds wheel/sdist, verifies metadata/package data, tests clean installation, and retains checksums plus compatibility reports. Configure the eventual PyPI project's Trusted Publishing integration and release environment. Version the Terraform module, sample, and docs with their Python-package compatibility.

Make mandatory AWS, failure-matrix, admin/browser, migration, and capacity evidence explicit release prerequisites. Keep workflow configuration separate from executing the first public release; this task must not upload an unverified artifact.

- [ ] **Acceptance:** A dry release produces the exact candidate artifacts and evidence manifest without publishing them. Missing/stale required evidence, a skipped mandatory E2E case, or an untested dependency range blocks release. CI publishes the tested artifact bytes rather than silently rebuilding different ones at upload time.

### DT-049: Rehearse the release from clean artifacts and infrastructure

**Dependencies:** DT-039–DT-048. **Specification:** Sections 18 and 20.

**Implement:** Run the complete release gate from the built candidate wheel/sdist and a clean checkout of the versioned sample/Terraform. Provision an isolated environment, migrate, deploy the digest-pinned image, run both worker profiles and mandatory failure/operations suites, collect metrics/log evidence, and tear down. Verify the support matrix and documentation against this candidate.

Produce a release evidence manifest linking requirement IDs, test runs, artifact hashes, image digest, infrastructure versions, known limitations, and cleanup results. Fix defects and repeat affected checks rather than waiving mandatory failures.

- [ ] **Acceptance:** Every required verification row is passing with evidence for the candidate or documented unchanged compatible artifacts. No mandatory scenario is skipped, packaging/migration/deployment works from scratch, and no unexplained test resource remains. The release-readiness decision refers to an identified artifact, not just a passing branch.

### DT-050: Publish and verify the first version

**Dependencies:** DT-049. **Specification:** Sections 14.7 and 20.

**Implement:** Perform the distinct release action for the selected PyPI project and version after reviewing candidate evidence. Publish the already tested distributions through the configured identity, tag the matching source, and publish versioned Terraform/sample documentation and release notes. State support boundaries, exclusions, and measured limitations honestly.

Install the public version in a fresh environment and run the consumer smoke scenario against its published artifacts. Confirm that the downloaded artifact hashes match the release manifest and that links/examples reference the released version.

- [ ] **Acceptance:** The distribution is retrievable from PyPI, fresh installation and sample smoke checks pass, the published source tag and artifact hashes agree, and the release evidence is retained. Publication is not complete merely because an upload command returned successfully.

## Suggested work streams and critical gates

The default starting sequence is DT-001 → DT-002 → DT-003/DT-004 → DT-005/DT-006 → DT-007/DT-008 → DT-009/DT-010. Establish the schema and configuration contracts before layering on delivery and operator features.

After that, use these work streams without changing their explicit dependencies:

| Work stream              | Main tasks    | Coordination point                                                                   |
| ------------------------ | ------------- | ------------------------------------------------------------------------------------ |
| Runtime correctness      | DT-011–DT-018 | Agreed transition services, token predicates, and worker response table.             |
| Consumer and artifacts   | DT-019–DT-021 | Public APIs, installed-package boundary, and immutable consumer image.               |
| Infrastructure           | DT-022–DT-028 | Shared route manifest, package entry points, network paths, and deployment workflow. |
| Developer operations     | DT-029–DT-037 | Shared action/query services, audit identity, permissions, and telemetry schema.     |
| Verification and release | DT-038–DT-050 | Requirement evidence, candidate artifacts, and mandatory release gates.              |

The first useful local checkpoint is DT-020. The first useful deployed checkpoint is DT-028. Neither permits skipping admin/CLI recovery, failure coverage, or clean-artifact verification in the first release. Actual PyPI publication remains the final task.

## Deliverable-to-task coverage

| Required capability                                   | Implementation tasks                                  | Main acceptance evidence                                                      |
| ----------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------- |
| uv package, public backend, wheel/sdist               | DT-001–DT-003, DT-010, DT-021, DT-040, DT-048–DT-050  | Clean installation, declared support matrix, artifact/release hashes.         |
| Task/attempt/control schema and migrations            | DT-005, DT-029, DT-035, DT-040                        | PostgreSQL constraints, migration and retention tests.                        |
| Import-path serialization and shared application code | DT-007, DT-014, DT-019, DT-021, DT-044                | Separate-process imports, installed sample, compatible rollout.               |
| Multiple producer-selected workers                    | DT-006, DT-010, DT-019, DT-022–DT-023, DT-028, DT-041 | Per-submission routing and persisted deployed worker identities.              |
| Transactional acceptance and enqueue deduplication    | DT-009–DT-010, DT-038–DT-039                          | Rollback/savepoint and concurrent conflict assertions.                        |
| Safe publication and durable delivery limits          | DT-011, DT-017, DT-038, DT-041, DT-043                | Lost-response/stale-token cases and delivery-hold evidence.                   |
| Claims, leases, retries, and ambiguous effects        | DT-008, DT-012–DT-016, DT-038–DT-039, DT-042          | Independent claims, expired ownership rejection, actual termination recovery. |
| Scheduled sweeper and invariant diagnostics           | DT-017–DT-018, DT-025, DT-028, DT-037, DT-044         | Missed-publication recovery, outage health, overlap/fairness tests.           |
| Standard SQS, batching baseline, and DLQ              | DT-015, DT-023, DT-041, DT-043                        | Event response tests, deployed queue configuration, real redrive behavior.    |
| Terraform, IAM, networking, secrets, and bootstrap    | DT-022–DT-028, DT-044, DT-046                         | Plan tests, clean apply/destroy, verified connectivity and scope.             |
| Actual consuming sample and local workflow            | DT-004, DT-019–DT-021, DT-026, DT-028, DT-047         | Fresh-checkout and installed-artifact scenarios with no copied protocol.      |
| Django admin, problem views, and actions              | DT-029–DT-030, DT-033–DT-034, DT-040, DT-043          | Browser recovery, read-only permissions, CSRF, idempotent audit.              |
| Inspection/recovery commands                          | DT-020, DT-031–DT-032, DT-040, DT-043                 | Installed commands, JSON/error contract, dry-run/no-side-effect assertions.   |
| Logs, CloudWatch links, metrics, and alarms           | DT-025, DT-036–DT-037, DT-043–DT-045                  | Correlated invocation links, redaction, triggered health signals.             |
| Unit, concurrency, artifact, and E2E coverage         | Every feature task; DT-003, DT-038–DT-044, DT-049     | Executable traceability for every specified transition and modeled failure.   |
| Capacity, cost, runbooks, and eventual PyPI release   | DT-045–DT-050                                         | Measured report, release rehearsal, published-artifact verification.          |

## What completion means

The project is implemented end to end when a developer can install the released package, configure the Django backend, enqueue to either named worker, deploy the supplied infrastructure, observe outcomes in admin/CLI/CloudWatch, and recover documented failures through audited tools. The sample must demonstrate that journey using the delivered package, and the release evidence must substantiate the claimed behavior.

An empty queue, a successful Lambda invocation, a passing mocked test suite, or a working blog example does not establish that outcome. The gates above require durable business/task assertions, genuine PostgreSQL concurrency, packaged consumer use, real AWS verification, and reproducible release artifacts.
