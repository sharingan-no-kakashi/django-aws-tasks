> Historical snapshot. Checkboxes preserve the original plan; current work belongs in GitHub Issues.

# LocalStack end-to-end testing backlog

Updated 23 October 2026. Add an emulated AWS integration tier alongside the
existing unit, database integration, provider-mocked Terraform, and real AWS
acceptance suites. This backlog complements the [core test plan](todo-task.md),
[workflow improvements](todo-workflows.md), and
[developer experience features](todo-features.md).

Use real database containers for task state and coordination. The package's
current engine targets are PostgreSQL 15+, MySQL 8.4+, MariaDB 10.6+, and Oracle
21c+. MySQL and MariaDB are first-class test targets. Select a database profile
for a local run and exercise the shared contract across the supported engine
matrix in CI. A passing reference-engine smoke test is not cross-engine
evidence.

Use LocalStack for SQS, DLQs, Lambda execution, Scheduler, and Secrets Manager.
Run the package's normal handlers from built application images. RDS is a
hosting service; local containers exercise engine behaviour, while compatible
RDS versions, privileges, settings, TLS, and failover need managed-service
verification. Additional engines or Aurora variants require explicit adapter
and deployment evidence. The minimal queue-to-worker tier should work before
adding the remaining service and failure scenarios.

This is implementation work still to do. Proposed paths, commands, markers, and
reports below do not describe an already working LocalStack environment.

| Milestone | Tasks | Completion gate |
| --- | --- | --- |
| Local infrastructure and packaging | LS-001–LS-007 | A packaged task reaches the correct worker through an emulated SQS event-source mapping |
| Behaviour and failure scenarios | LS-008–LS-015 | Durable task, attempt, business-effect, and workflow assertions pass through the emulated service path |
| CI and developer operation | LS-016–LS-018 | Reproducible execution, useful failure artifacts, reliable cleanup, and explicit evidence provenance |
| Database portability | LS-019–LS-020 | Shared tests and sample/deployment configuration work with the selected engine instead of assuming PostgreSQL |

Dependencies apply to completing a task; scaffolding can start earlier. WF
references belong to the workflow backlog. Each implementation includes its
relevant tests and documentation. Keep execution at batch size one and preserve
the production timing constraints, including when using a shorter fault-test
profile.

Current LocalStack setup requires an authentication token and an applicable
license; CI uses a CI token. Record the chosen version, service coverage, and
entitlements explicitly. Relevant upstream references are the
[Lambda coverage](https://docs.localstack.cloud/aws/services/lambda/),
[Scheduler guide](https://docs.localstack.cloud/aws/services/scheduler/),
[queue networking guide](https://docs.localstack.cloud/aws/services/sqs/), and
[authentication guide](https://docs.localstack.cloud/aws/getting-started/auth-token/).

## LS-001: Define the emulated test contract and service coverage

**Dependencies:** None.

Inventory the AWS APIs and behaviours used by the package, Terraform module,
sample adapters, and selected scenarios. Select a pinned LocalStack release and
record required service coverage, licensing, Docker capabilities, and supported
host architectures. Define separate local-emulation and real-AWS test targets.
Record database image, engine, driver, and configuration versions for every
claimed matrix cell, including the engine-specific startup requirements.

- [ ] **Acceptance:** A coverage table identifies supported, unsupported, and
  unverified scenarios. SQS event-source mappings and container-image Lambda
  execution are confirmed for the selected environment. The plan identifies
  real-AWS-only obligations, including networking, failover, service-specific
  termination, alarm delivery, scaling, and measured capacity/cost evidence.

## LS-002: Add isolated Compose infrastructure and networking

**Dependencies:** LS-001.

Add Compose database profiles for PostgreSQL, MySQL, MariaDB, and Oracle alongside
LocalStack. Configure engine-appropriate drivers, ports, credentials, timezones,
isolation, and required storage/comparison settings. Arrange a shared Docker
network that also reaches Lambda runtime containers. Add bounded readiness
checks, unique project/run names, isolated databases and volumes, and
deterministic teardown. Keep emulator activation credentials separate from the
dummy AWS credentials used by SDK clients.

- [ ] **Acceptance:** Each selected database profile and LocalStack start from a
  clean environment, and a Lambda container can resolve and reach both. MySQL
  and MariaDB run as distinct engines. Two runs do not share state. Setup
  failures explain missing drivers, Docker, activation, architecture, or network
  prerequisites. Unavailable required profiles fail their matrix cell rather
  than silently falling back to another engine.

## LS-003: Configure SDK endpoints consistently across processes

**Dependencies:** LS-001, LS-002.

Provide explicit endpoint configuration for the test runner, producer, workers,
sweeper, and sample adapters. Cover SQS, Lambda, STS, Secrets Manager, Scheduler,
logs, and the image registry where used. Reuse the transport's existing endpoint
seam and documented SDK configuration where possible. Retain AWS defaults for
normal deployments.

- [ ] **Acceptance:** Local runs succeed with dummy AWS credentials and no real
  AWS profile. Record the selected test target and endpoints; local execution
  cannot silently fall back to real AWS. Production tests still select their
  original clients and resource identities. Host and container endpoints work
  from their respective network locations.

## LS-004: Support emulator queue identities without weakening production checks

**Dependencies:** LS-003.

Resolve the current HTTPS/account/name queue URL checks against the chosen
LocalStack URL strategy. Prefer compatible HTTPS URLs where practical; if a
local validation mode is needed, make it explicit and limited to configured
emulator endpoints. Preserve distinct queue identities, ARN/URL consistency,
standard queue requirements, and all lease/timeout validation.

- [ ] **Acceptance:** Both named local routes pass validation and are reachable
  from producer and worker containers. Production configuration still rejects
  invalid schemes and inconsistent identities. A task payload cannot select an
  arbitrary endpoint or enable emulator validation.

## LS-005: Build and deploy the real consumer Lambda image locally

**Dependencies:** LS-001, LS-002, LS-003.

Reuse the sample Dockerfile and identified wheel/source candidate. Make the
built image available to LocalStack, using its local registry path where
required. Record image identity, distribution hashes, runtime base, and
architecture. Select worker and sweeper entry points through function
configuration, as in the deployed application.
Build with the selected engine's driver dependencies and configure Django's
database backend and connection options accordingly. Record driver locks and
hashes; do not rely on the current PostgreSQL-only sample dependency set.

- [ ] **Acceptance:** Lambda invocations import the installed package and the
  application's task modules from the image. No editable checkout mount or
  copied worker implementation is required. Unsupported image execution fails
  the setup check; it does not silently substitute direct Python execution.

## LS-006: Provision a minimal emulated task stack

**Dependencies:** LS-002, LS-003, LS-004, LS-005.

Add a local Terraform root or equivalent reproducible provisioning fixture for
two source queues, their DLQs, named worker functions, event-source mappings,
and a sweeper function. Reuse the existing module or extract shared protocol
resources if necessary. Supply the selected real database through Compose;
avoid depending on an emulated RDS/NAT data path. Pass its actual host, port,
engine, and engine-specific connection options. Emit a runtime manifest and
deployment outputs that the test harness can consume.

- [ ] **Acceptance:** Provisioning is repeatable and produces distinct routes
  with batch-one mappings and the expected partial-response configuration.
  Manifest-to-runtime checks match the shared deployment contract. Local state
  is isolated, all provider requests target the emulator, and destruction leaves
  no test-owned queues, functions, mappings, or other provisioned resources.

## LS-007: Add a distinct pytest harness and target-aware scenario helpers

**Dependencies:** LS-003, LS-004, LS-005, LS-006.

Add a separate suite such as `tests/localstack/` and an explicit pytest marker.
Share submission, invocation, bounded polling, inspection, and business-state
assertions with existing scenarios where appropriate. Keep real-AWS fixture
selection and evidence output explicit. Add run IDs, task IDs, overall
deadlines, and cleanup that runs after setup or scenario failures.
Use a generic database-alias fixture and engine-aware connection helpers;
PostgreSQL-only helpers must not be used to exercise MySQL or MariaDB.

- [ ] **Acceptance:** A selected mandatory local suite fails early when the
  emulator is unavailable; ordinary unit runs do not start it implicitly. Tests
  can inspect durable state without a public application endpoint. Local and
  real-AWS jobs cannot accidentally select each other's deployment workspace.

## LS-008: Prove normal delivery, routing, and transaction rollback

**Dependencies:** LS-007.

Submit application tasks through the producer, publish through SQS, and let
LocalStack's event-source mappings invoke the named Lambda workers. Exercise
producer route overrides and business/task transaction rollback. Verify the
installed entry points and application modules along this path.

- [ ] **Acceptance:** On each supported database profile, each route records
  execution by its intended function,
  correct attempt metadata, and one protected business effect. Rolled-back
  business work leaves no executable task. Queue emptiness or a successful
  invocation alone cannot satisfy these assertions, and direct handler calls
  are not substituted for event-source delivery.

## LS-009: Exercise duplicate delivery, poison messages, and redrive

**Dependencies:** LS-008.

Send controlled duplicate notifications and malformed, unknown-task, and
wrong-route messages. Exercise visibility and redrive behaviour using the
validated test profile. Include a worker initialization failure that prevents
claims. Inspect both transport deliveries and durable execution history.

- [ ] **Acceptance:** Duplicate hints do not create additional committed
  attempts after successful work. Delivery failures reach the expected DLQ
  under emulated service behaviour. A worker that never claims work does not
  consume a logical execution attempt, and wrong-route delivery cannot execute
  a task under another worker's policy.

## LS-010: Exercise application retries, failures, and ambiguity

**Dependencies:** LS-008, LS-009.

Run sample tasks that fail transiently, fail permanently, and report uncertain
external outcomes. Include a controlled provider that accepts an operation but
loses its response. Use the real worker classification and owned finalization
paths, with protected and unprotected business effects.

- [ ] **Acceptance:** Retry timing, attempt limits, terminal states, and holds
  match the declared policies. Early or stale notifications cannot bypass due
  times. Protected repetition preserves one intended effect; unprotected
  uncertainty requires review. Reports identify application fault injection
  separately from behaviour supplied by the emulator.

## LS-011: Verify Scheduler-driven sweeper recovery

**Dependencies:** LS-007, LS-008.

Provision an emulated Scheduler target for the normal sweeper handler. Submit
durable work with immediate publication disabled and verify eventual execution.
Use the emulator's explicit trigger for fast deterministic cases, and retain a
separate case that waits for actual scheduled delivery.

- [ ] **Acceptance:** Scheduler invokes the sweeper, the sweeper publishes a
  hint, and the worker finishes the accepted task. Explicit-trigger cases are
  labelled as such and do not stand in for scheduled-delivery evidence.
  Missing required Scheduler coverage fails the selected suite clearly.

## LS-012: Verify abrupt worker loss, lease recovery, and audited release

**Dependencies:** LS-009, LS-010, LS-011.

Terminate a test-owned runtime or use a controlled sample task that exits after
claiming and, separately, after performing an effect. Observe recovery through
the sweeper after lease expiry. Exercise delivery-budget exhaustion, an audited
hold release, and protected replay through the normal control services.

- [ ] **Acceptance:** A live lease is not reclaimed. Expiry closes the original
  attempt as abandoned and leads to the configured retry or ambiguity hold.
  Recovery preserves operation identity and does not guess an external outcome.
  Faults affect only the isolated run. Local process loss is not labelled as
  evidence of AWS hard-timeout, OOM, or database-failover behaviour.

## LS-013: Run workflow scenarios through separate Lambda invocations

**Dependencies:** LS-010, LS-011, LS-012; WF-001, WF-002, WF-003.

Enable the workflow app in all participating producer, worker, and sweeper
images. Exercise a chain, a parallel section and join, output references,
failure propagation, a held parent and release, and recovery after a missed
advancement callback. Include competing advancement and an incompatible saved
step alongside a healthy workflow.

- [ ] **Acceptance:** Children are scheduled once and only after their required
  parents succeed. The join waits for all parents, required outputs survive
  retention, and a broken run cannot starve another. Tests use the workflow
  features actually implemented; future retry/cancellation features gain
  additional scenarios when those features are delivered.

## LS-014: Exercise secret loading and credential refresh

**Dependencies:** LS-003, LS-005, LS-007, LS-008.

Store database and application signing material in emulated Secrets Manager and
load them through the existing application integration. Verify cold startup
and warm credential refresh using a controlled credential change on the selected
database engine. Bound any test-only cache settings and include invalid or
missing secret cases.

- [ ] **Acceptance:** Functions authenticate to the selected real database
  using the retrieved credentials, refresh changed credentials, and recycle connections
  as required. Database password changes do not change the signing secret.
  Logs and retained reports omit secret values. Results do not claim managed
  RDS rotation or failover coverage.

## LS-015: Exercise compatible image and schema upgrades

**Dependencies:** LS-005, LS-008, LS-011; WF-003.

Reuse the sample's original/compatible image fixture and additive migration
scenario. Preserve old queued and held tasks across worker/producer replacement,
import shims, migration, and rollback. Include saved workflow definitions when
the workflow extension is selected. Record both image identities and schema
observations.

- [ ] **Acceptance:** Compatible images execute previously accepted work with
  unchanged routing and business identities. Held work remains inspectable and
  recoverable, rollback preserves the additive schema, and unsupported saved
  contracts produce explicit diagnostics. This supplies local compatibility
  evidence without satisfying the real-AWS rollout gate.

## LS-016: Add reproducible CI execution and failure artifacts

**Dependencies:** LS-007, LS-008, LS-009, LS-010, LS-011, LS-012, LS-013, LS-014, LS-015, LS-019, LS-020.

Add a dedicated CI job using the pinned emulator and candidate application
image. Configure the required CI activation token, isolated namespaces, bounded
job duration, and an explicit policy for runs without access to that token.
Collect JUnit, emulator/runtime logs, deployment manifests, task/attempt/effect
observations, image hashes, and cleanup results. Always tear down owned state.
Parameterise jobs by database engine and version, including distinct MySQL and
MariaDB cells. A small reference-engine smoke job may run first, but the claimed
cross-engine contract requires the corresponding matrix results.

- [ ] **Acceptance:** A trusted CI run executes the selected cases without a
  real AWS account. Missing required prerequisites or skipped mandatory cases
  cannot produce a passing local-E2E result. Failure artifacts survive teardown,
  parallel runs stay isolated, and secrets are not exposed to untrusted jobs or
  printed in logs.

## LS-017: Keep emulated and real-AWS evidence distinct

**Dependencies:** LS-007, LS-016.

Add explicit execution-target provenance to scenario reports and candidate
receipts. Record emulator version, endpoints, image identity, run ID, database
engine/version/driver, and scenario coverage. Extend traceability without
allowing LocalStack reports to satisfy requirements that demand genuine AWS
observations. Preserve the
existing mandatory AWS and capacity suites.

- [ ] **Acceptance:** The local suite has a distinct identity, and renaming a
  local report or receipt as an AWS suite does not make it acceptable cloud
  evidence. Verification tests reject mismatched or missing required provenance
  according to the versioned receipt contract. Optional local results cannot
  erase missing AWS, IAM/networking, alarm, failover, or capacity evidence.

## LS-018: Publish developer commands and test-layer guidance

**Dependencies:** LS-016, LS-017.

Add documented commands such as `make test-localstack` and corresponding
setup/teardown targets. Cover activation, image builds, supported architectures,
database profile selection, ports, Docker networking, state reset, diagnostics,
and cleanup. Explain which suite to run for a code change and which scenarios
still require real AWS.
Link LocalStack usage to the existing testing helpers without making it a
prerequisite for every unit test.

- [ ] **Acceptance:** Another developer can run the suite from a clean checkout
  and diagnose a failed Lambda-to-database or queue connection. The command
  returns a useful result and cleans up after failure. Documentation states
  that AWS account credentials are unnecessary while LocalStack activation and
  build dependencies are still required; it makes no unverified offline claim.

## LS-019: Parameterise the shared database contract suite

**Dependencies:** LS-002, LS-007.

Extract engine-independent fixtures and assertions for migrations, enqueue,
deduplication, rollback, claims, leases, finalization, reconciliation, retention,
and enabled workflows. Use the selected Django database alias and independent
connections. Keep genuinely engine-specific SQL and adapter tests in explicitly
named suites. Preserve existing requirement IDs and test assignments when
reorganising the current PostgreSQL-based tests.

- [ ] **Acceptance:** The shared contract runs against real MySQL and MariaDB
  as well as PostgreSQL and Oracle profiles. It checks NULL/key comparison,
  lock contention, database time, writer validation, and restoration of session
  limits. An engine-specific failure cannot be hidden by running the case on
  PostgreSQL, and a skipped required matrix cell cannot establish support.

## LS-020: Make sample and deployment configuration engine-aware

**Dependencies:** LS-005, LS-006, LS-019.

Remove PostgreSQL-only assumptions from the sample's selected database
configuration, image drivers, database secret handling, and Terraform module
inputs. Pass database port explicitly instead of hardcoding 5432, and configure
TLS and connection options using the selected Django backend. Support reusing
an existing compatible RDS database; any provisioning example must declare its
engine/version and required parameter, option, and licensing settings.

Coordinate with FT-005's existing-infrastructure recipe. An RDS service name
alone does not establish engine compatibility: validate the chosen versions,
privileges, isolation, clock, comparison, and writable-primary requirements.
Aurora variants or additional RDS engines need their own compatibility decision
and evidence before being advertised.
Managed-service verification belongs to the core real-AWS acceptance work and
does not require an AWS account to run the LocalStack suite.

- [ ] **Acceptance:** LocalStack producer, worker, and sweeper images use each
  selected engine without editing application source. Terraform plan tests
  exercise MySQL/MariaDB ports and configuration as well as PostgreSQL, and do
  not provision or replace a supplied existing database. The core real-AWS
  acceptance plan records the exact RDS engine/version and requires shared
  protocol checks with an appropriately scoped writer. Unverified combinations
  remain explicit gaps rather than receiving a generic RDS support claim.
