# Backend configuration and execution reference

Install the backend with its engine-specific driver extra, add `django_tasks_aws` to
`INSTALLED_APPS`, configure `TASKS`, and run migrations. Include the same consumer
application tasks in every producer and worker artifact. PostgreSQL 15+, MySQL 8.4+, MariaDB 10.6+, or Oracle 21c+ on a writable primary is required;
SQLite is not a coordination backend. See [database adapters](database-backends.rst)
for configuration, identifier limits, and upgrade instructions. PostgreSQL 15
and 16 are covered by integration CI. MySQL, MariaDB, and Oracle adapters are
experimental. This is a personal prototype; do not use it in production. See the
[testing guide](testing.rst) for checks and recorded results.

Task acceptance saves the `takes_context` execution contract. Workers hold work
as `incompatible_payload` before calling the handler if the saved contract is
malformed or differs from the deployed task. Operator replay preserves the
saved contract. Older development records with empty `task_options` have no
contract to compare and retain legacy behavior; drain or recreate that work
before changing task context signatures.

Execution returns an `ExecutionOutcome` string enum: `success`, `retry`, `hold`,
`failure`, or `stale`. A rejected ownership-checked finalization returns `stale`,
including retry, failure, and hold transitions. This result describes the
confirmed database transition, not just the requested transition.

```python
TASKS = {
    "default": {
        "BACKEND": "django_tasks_aws.backends.DatabaseSqsBackend",
        "QUEUES": ["short", "documents"],
        "OPTIONS": {
            "database_alias": "default",
            "workers": WORKER_ROUTES,
            "allowed_task_modules": ["myapp.tasks"],
            "policies": {
                "myapp.tasks.render_document": {
                    "allowed_routes": ["documents"],
                    "ambiguous_retry": True,
                    "operation_key_resolver": "myapp.policies.document_key",
                },
            },
        },
    },
}
```

`WORKER_ROUTES` is the `route_manifest` Terraform output, decoded as a mapping.
Every route needs `source_queue_arn`, `source_queue_url`, `timeout_seconds`,
`lease_seconds`, `visibility_timeout_seconds`, `max_receive_count`, `concurrency`,
`dispatch_limit`, and `redispatch_seconds`. Sources must be distinct standard
queues, and URL/account/name must match their ARN. Timeout must exceed the five-second finalization allowance. Lease must cover
timeout plus five seconds; visibility must be at least six times timeout, and transport receives
must be bounded between 5 and 1000. The Terraform
module validates the deployment resource bounds as well.

Worker profiles and policy overrides use strict Pydantic validation. Supply
actual booleans and integers: strings such as `"false"` and `"5"`, and booleans
in integer fields, are rejected with `ConfigurationError`. Unknown profile
fields are rejected to catch misspelled options. Policy sequences accept lists
or tuples, but their elements are not coerced.

| Backend option | Default | Purpose |
| --- | --- | --- |
| `database_alias` | `default` | Writable SQL database connection for atomic acceptance and coordination |
| `workers` | Required | Trusted route manifest; names must exactly match `QUEUES` |
| `allowed_task_modules` | Required | Explicit import module/prefix allowlist |
| `max_attempts` | 5 | Durable claim budget for newly accepted work |
| `policies` | Empty overrides | Per-import-path task policy |
| `immediate_dispatch` | true | Best-effort publication after acceptance commits |
| `paused_routes` | Empty | Prevent new claims/publications on selected routes |
| `producer_version` | `TASK_APPLICATION_VERSION`, then package version | Application release that created the work |
| `credentials_resolver` | None | Optional importable `(database_alias) -> None` credential refresh hook at invocation entry |
| `result_retention_days` | 30 | Public result/replay window |
| `failure_diagnostic_retention_days` | 90 | Failed attempt/error detail retention |
| `audit_detail_retention_days` | 90 | Actor/reason detail retention |
| `overdue_grace_seconds` | 300 | Inspection threshold for overdue runnable work |

Policies accept `allowed_routes`, `payload_versions` (initial argument envelope
version 1), `max_attempts`, `retryable_exception_paths`,
`permanent_exception_paths`, `retry_safe`, `ambiguous_retry`, and
`operation_key_resolver`. Operation keys must identify the intended business
effect and survive replay; they do not provide remote idempotency by themselves.
Permanent and retryable paths identify declared exception classes. Unexpected
failures retry only under the declared retry-safe policy and remaining budget.

The Lambda worker and sweeper select `TASK_BACKEND_ALIAS` (default `default`).
Workers additionally require `TASK_WORKER_NAME` and `TASK_WORKER_SOURCE_ARN`.
A backend owns its database rows even if another backend uses the same route
names. Supply `backend_alias` with that backend's database, routes, and policies
when directly invoking reconciliation/publication services.

Handlers can protect downstream work using the active execution budget:

```python
from django_tasks_aws.services.execution import get_remaining_seconds, require_time

require_time(3)
# Bound both connection and request timeouts below this allowance.
timeout = min(10, get_remaining_seconds())
```

The allowance excludes five seconds reserved for finalization. The worker
refreshes the Lambda context budget around execution. `tasks_worker` uses the
same compatibility and claim service and a hard Unix process timer, and must run on
its process's main thread. A timed-out command terminates without Python cleanup; reconciliation closes
its abandoned claim. Local task bodies must not replace the timer or start work that can outlive the process; independently suspended
or arbitrary long-lived workers remain excluded. A timeout can still leave
remote effects uncertain; the task's ambiguity policy controls recovery.

Enqueue inside the business transaction on the configured writer connection.
An ID returned before the outer commit is not durable acceptance. Delayed work
uses `.using(run_after=...)`, route overrides use `.using(queue_name=...)`, and
submission deduplication uses `django_tasks_aws.submit(task, args=(...), key="...", scope="...")`.
Django enqueue, public submission, and workflow retry share one acceptance service
that validates task capabilities, route policy, and the module allowlist before persistence.
Different work/routing under an accepted key conflicts. Expired repeated keys
retain their task identity and raise `ExistingTaskResultExpired`.
