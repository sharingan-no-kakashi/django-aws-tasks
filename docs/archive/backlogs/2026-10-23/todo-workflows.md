> Historical snapshot. Checkboxes preserve the original plan; current work belongs in GitHub Issues.

# Workflow improvement backlog

Updated 23 October 2026. This backlog covers the optional
`django_tasks_aws.workflows` app and complements the core
[implementation backlog](todo-task.md). The current API and behaviour are
documented in [the workflow guide](../../../workflows.rst).
General developer experience and product additions are tracked in the
[feature backlog](todo-features.md).
Workflow execution through emulated AWS services is planned in the
[LocalStack testing backlog](todo-localstack.md).

Start with failure isolation and verification, then improve recovery and the
operator experience. Keep the supported scope to static graphs whose steps are
known at submission time. Dynamic expansion, conditional branches, human
approvals, and compensation remain separate future work.

The current tree already includes workflow integration tests, the Oracle
submission-key migration, and typing fixes. These are the starting point for
this backlog. The tasks below do not claim that their acceptance evidence has
been collected. Proposed APIs and operator actions are future work.

| Order | Tasks | Outcome |
| --- | --- | --- |
| 1 | WF-001–WF-005 | Failures are isolated, execution is bounded, and the supported contract has evidence |
| 2 | WF-006–WF-011 | Developers can recover, inspect, and manage workflows through supported interfaces |
| 3 | WF-012–WF-014 | A developer can try the feature, understand its limits, and evaluate a verified release candidate |

P1 items address correctness or required evidence. P2 items improve everyday use.
Dependencies apply to completing a task; interface design can start earlier.
Check an acceptance box only when its implementation, documentation, and
relevant verification are complete.

## WF-001 — P1: Isolate incompatible steps and broken workflow candidates

**Dependencies:** None.

Handle Django's `InvalidTask` when restoring or enqueueing a saved step. A
retired queue or another expected compatibility failure should become a recorded
preparation failure. Preserve the saved destination and arguments. Ensure a
failure in one candidate does not stop later workflow candidates in the sweep.
Surface unexpected errors without reporting successful progress or guessing a
repair.

- [ ] **Acceptance:** Submit an old workflow, deploy a valid configuration that
  removes its waiting step's queue, and reconcile it alongside a healthy run.
  The incompatible step records an actionable error, the healthy run advances,
  and subsequent sweeps remain fair. Add coverage for changed task contracts
  and confirm ordinary task reconciliation and dispatch still proceed.

## WF-002 — P1: Extend the existing workflow tests through real execution

**Dependencies:** WF-001.

Review and extend `tests/integration/test_workflows.py` and
`tests/unit/test_workflow_constraints.py`. Several existing coordinator tests
set task states directly; add cases that claim tasks, execute handlers, finalize
attempts, and trigger the real lifecycle and retention signals. Cover graph
composition, inferred dependencies, cycle rejection, output selection, invalid
arguments, graph limits, independent branches, and advancement across several
batches.

- [ ] **Acceptance:** Tests exercise automatic retries, terminal failures,
  ambiguity holds, audited release, missed callbacks, and actual cleanup of
  expired details. Independent database connections prove competing
  advancement schedules one child. Root submission and child scheduling roll
  back together on failure. CI collects and runs the workflow cases and does
  not treat a skipped database suite as passing evidence.

## WF-003 — P1: Define the versioned workflow contract

**Dependencies:** None.

Document run and node states, submission identity, output references, failure
propagation, and compatibility rules. Explain that parallel steps become
eligible together and that saved `run_after` values are absolute scheduling
constraints. Define how future retry and cancellation actions interact with
running tasks, completed steps, operation keys, and retained results. Preserve
the existing distinction between ordinary task replay and workflow recovery.

- [ ] **Acceptance:** The workflow guide, implementation specification, and
  acceptance status agree on the optional extension's support boundaries.
  Workflow requirements have stable IDs mapped to executable tests. Existing
  core requirement IDs remain intact, and unsupported features are explicit.

## WF-004 — P1: Bound workflow advancement by the invocation budget

**Dependencies:** WF-001, WF-003.

Pass the remaining invocation budget into completion-driven advancement as well
as scheduled reconciliation. Bound graph reads, child preparation, database
waits, and publication work. Stop preparing new children before finalization
time is exhausted and leave durable work for the next sweep. Preserve fairness
when a large or slow workflow shares a sweep with small runs.

- [ ] **Acceptance:** Near-deadline callbacks safely defer advancement. Slow
  database or transport operations do not cause an unbounded sequence of child
  submissions. A later sweep finishes deferred work without duplicate logical
  children. Parent completion remains durable even when its advancement
  callback cannot finish.

## WF-005 — P1: Verify workflow schema and behaviour on supported databases

**Dependencies:** WF-002, WF-003, WF-004.

Run the workflow contract against each database engine claimed for this
extension. Exercise fresh migrations and upgrades through the Oracle
submission-key correction. Include nullable submission identities, exact key
comparison, locking, concurrent advancement, and retention queries. Record
engine and driver versions. Narrow the documented workflow support matrix if
an engine does not have passing evidence.

- [ ] **Acceptance:** Every advertised engine has recorded migration and
  lifecycle results, including multiple unkeyed submissions and competing
  advancement. SQLite is not used as locking evidence. Later schema changes
  repeat the affected checks before release.

## WF-006 — P1: Add audited retry of failed workflow steps

**Dependencies:** WF-001, WF-002, WF-003, WF-005.

Provide a workflow recovery service that retries an eligible failed step and
reopens descendants skipped because of that failure. Reuse successful parent
outputs and leave successful branches complete. Retain the original task and
an execution history linking replacements to their workflow steps. Preserve
the intended business operation key; require effect review where the outcome
is uncertain. Keep the accepted graph and arguments immutable.

Define the recovery window together with retention. Reject retry when required
inputs or parent results have expired, and explain which data is missing.

- [ ] **Acceptance:** For `A succeeds → B fails → C is skipped`, an audited
  recovery creates replacement work for B and eventually schedules C without
  rerunning A. Repeating the same recovery request creates no extra work.
  Concurrent recovery and reconciliation remain safe. Original task history
  stays inspectable, and expired inputs or unresolved external effects cannot
  silently produce a new operation.

## WF-007 — P2: Support cancellation and abandonment

**Dependencies:** WF-002, WF-003, WF-004, WF-006.

Add audited, idempotent workflow cancellation through a shared service. Stop
new child scheduling and prevent unclaimed associated work from starting.
Define cancellation while attempts are already running: preserve their lease
ownership and record their eventual outcomes. Explain the distinction between
requesting cancellation and reaching a settled cancelled state. Provide a
supported way to close abandoned blocked workflows and apply retention.

- [ ] **Acceptance:** Cancellation races with advancement, claims, and
  finalization are covered. Stale deliveries cannot start cancelled pending
  work, and active attempts are not forcibly reassigned. Repeated requests
  return the original result. Settled cancellation cannot be reversed by a
  delayed callback or sweep.

## WF-008 — P2: Add readable workflow and step names

**Dependencies:** WF-003, WF-005.

Allow display names for a workflow and its steps, plus a searchable business
reference. Keep presentation metadata distinct from execution identity.
Metadata arguments must not consume keyword arguments intended for task
functions. Preserve existing `step()` calls and stored graphs through an
additive migration.

- [ ] **Acceptance:** Two uses of the same task function can have distinct,
  useful names in inspection output. Existing graphs still load. Renaming a
  display label cannot retarget accepted work, and the effect of metadata on
  submission identity is documented and tested.

## WF-009 — P2: Provide a public status and outputs API

**Dependencies:** WF-003, WF-008.

Provide a documented read interface for run progress, named steps, dependencies,
blocking reasons, current task identities, and available outputs. Callers
should not need to assemble these details from several ORM queries. Define
refresh behaviour, missing-run errors, retention expiry, and access to
sensitive data. Return bounded snapshots suitable for polling and web views.

- [ ] **Acceptance:** A consumer can display progress and retrieve a named
  successful output through public APIs. Waiting, failed, held, cancelled, and
  expired states have distinct responses. Queries respect the selected backend
  and database, and default summaries omit sensitive arguments and results.

## WF-010 — P2: Add workflow inspection and recovery to admin and CLI

**Dependencies:** WF-006, WF-007, WF-009.

Add workflow list and detail pages showing the dependency graph, step states,
blocking reasons, timestamps, and links to task attempts and logs. Add filters
for name, business reference, status, age, and backend. Extend commands with
bounded listing and structured JSON output. Expose retry and cancellation
through the same audited services used by the UI, with previews and dry runs.

- [ ] **Acceptance:** A read-only operator can identify the blocking step and
  reach its task diagnostics. Recovery requires the appropriate permission and
  a concrete review of the intended change. Browser and CLI cases verify
  permissions, sensitive-data access, CSRF, idempotent requests, and dry runs
  without side effects. No action requires editing protocol rows directly.

## WF-011 — P2: Expose workflow progress and stalled recovery in telemetry

**Dependencies:** WF-001, WF-004, WF-006, WF-007, WF-009.

Correlate workflow and step identities with task events. Record preparation
failures, blocked runs, deferred advancement, recovery requests, cancellation,
and reconciliation errors. Add bounded metrics for advancement latency,
unfinished runs, and stalled recovery. Distinguish intentional scheduling and
operator holds from a coordinator that is no longer making progress. Keep
individual workflow IDs in logs rather than metric dimensions.

- [ ] **Acceptance:** An operator can trace a workflow through its task attempts
  and explain why it is waiting. A broken coordinator produces a useful signal
  even when ordinary tasks still run. Logs omit sensitive inputs and outputs,
  and metric dimensions remain bounded as workflow count grows.

## WF-012 — P2: Add a guided local workflow example

**Dependencies:** WF-006, WF-007, WF-010.

Extend the sample with a small pipeline containing a parallel section and a
join. Show successful execution, automatic retry, a held step and release,
terminal failure and workflow recovery, and cancellation. Display progress
without manual page refresh. Add an existing-application tutorial covering app
registration, migration, task reuse, scheduling, and inspection.

- [ ] **Acceptance:** A developer starts the example without AWS credentials,
  sees the graph advance, and follows a recovery scenario through the supplied
  UI or commands. The guide distinguishes current features from future work
  and explains submission keys, operation keys, and workflow recovery requests
  with concrete examples.

## WF-013 — P2: Measure workflow overhead and publish practical limits

**Dependencies:** WF-004, WF-005, WF-011.

Measure chains, parallel sections with a join, and sweeps containing many active
or blocked runs. Include the default graph and advancement limits. Record
database query counts, lock contention, callback time, child scheduling delay,
and sweeper throughput. Measure callback publication overhead separately from
handler execution. Use the results to justify defaults and deployment guidance.

- [ ] **Acceptance:** A reproducible report identifies the tested environment,
  workload, and observed limits. It shows that deferred work progresses fairly
  and distinguishes dependency readiness from queue execution time. Performance
  claims refer to measurements rather than graph syntax or local smoke checks.

## WF-014 — P1: Gate workflow support on installed and deployed evidence

**Dependencies:** WF-005, WF-006, WF-007, WF-010, WF-011, WF-012, WF-013.

Verify the completed feature from the exact candidate wheel and source
distribution, including optional app registration, migrations, commands, and
admin assets. Exercise a deployed workflow across named workers, lost
advancement callbacks, held-step recovery, failed-step retry, cancellation, and
a compatible application upgrade. Bind results to the candidate artifacts and
the advertised support matrix.

- [ ] **Acceptance:** Workflow requirements resolve to passing test reports and
  retained deployment evidence. Missing or skipped mandatory cases block the
  workflow support claim. Fresh installation and schema upgrade both work,
  compatible deployment preserves accepted graphs, and disposable resources
  are cleaned up with recorded results. Release notes state the supported
  scope and measured limits.
