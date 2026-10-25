# django-tasks-aws: Implementation Specification (v1)

> Status: **frozen contract (DT-001)**. This is the versioned, distilled contract
> derived from [`../Django tasks design.md`](../Django tasks design.md). The full
> design remains the normative protocol reference; this file fixes the decisions
> the first release must not silently change. Requirement IDs resolve in
> [`verification-matrix.yaml`](verification-matrix.yaml).

## 1. Naming

| Concern            | Value              | Evidence                                                        |
| ------------------ | ------------------ | --------------------------------------------------------------- |
| Distribution name  | `django-tasks-aws` | PEP 503 normalized wheel/sdist name; `uv add django-tasks-aws`  |
| Import namespace   | `django_tasks_aws` | Source layout at `src/django_tasks_aws/`                        |
| Django app label   | `django_tasks_aws` | `apps.py` `DjangoTasksAwsConfig.name`                           |
| Backend class path | `django_tasks_aws.backends.DatabaseSqsBackend` | `TASKS["default"]["BACKEND"]`            |
| Worker handler     | `django_tasks_aws.handlers.worker.lambda_handler`               | Lambda SQS entry point                 |
| Sweeper handler    | `django_tasks_aws.handlers.sweeper.lambda_handler`              | Scheduler entry point                  |

The singular `django_task_aws` spelling used by earlier prototypes is retired.
PyPI availability is not yet claimed; the name is treated as a working label
until DT-048 performs the release check.

## 2. Support boundaries

Support targets for the first release (full matrix evidence is still required):

- **Python** `>=3.12,<3.15` (CPython 3.12, 3.13, 3.14).
- **Django** `>=6.1,<7` (the `django.tasks` API: `task`, `Task`, `TaskResult`,
  `TaskResultStatus`, `BaseTaskBackend`, `TaskContext`, `.using(...)`).
- **PostgreSQL** 15+, **MySQL** 8.4+, **MariaDB** 10.6+, or **Oracle** 21c+
  on a writable primary. MySQL/MariaDB require InnoDB.
  Shared ORM coordination uses engine adapters; see [database backends](database-backends.rst).
  The existing CI suite remains PostgreSQL-based. SQLite may be used for unit
  tests only, never as evidence of locking behavior.
- **AWS** standard SQS (not FIFO), Lambda (Python runtime), EventBridge
  Scheduler, CloudWatch Logs/Metrics.

Compatibility outside these bounds is not claimed. The full Python/Django CI
matrix remains a release requirement; installed-artifact CI covers Python 3.12–3.14,
and the candidate workflow covers PostgreSQL 15/16 and both dependency bounds.

## 3. Initial execution contract

The first release implements the narrow contract below. Anything broader is a
separate, independently claimed feature.

1. **Synchronous, importable tasks** resolved by `module_path` at enqueue time.
2. **Standard queues** with at-least-once delivery; no ordering guarantee.
3. **Batch size one** per worker invocation, zero batch window.
4. **Database-scheduled retries** with bounded backoff + jitter.
5. **Fixed leases** sized to each worker's timeout plus margin; no heartbeat.
6. **Two named workers** (`short`, `documents`) with distinct queues/functions.
7. **Explicit ambiguity handling** (retry vs. `ambiguous_external_outcome` hold).

## 4. Explicit exclusions

The first release does **not** provide:

- Global ordering or cross-database transactions. Workflow graphs are an optional
  app with the separate contract in section 9.
- Exactly-once external effects, or automatic compensation.
- Priority scheduling, coroutine/async handlers, or strict requests-per-second
  limiting (concurrency control only).
- FIFO queues or per-resource ordering.
- Tasks whose execution can outlive the configured worker deadline.
- Automatic resolution of ambiguous downstream outcomes when the provider offers
  no identity/query path.

## 5. API and settings outline

```python
INSTALLED_APPS = [..., "django_tasks_aws"]

TASKS = {
    "default": {
        "BACKEND": "django_tasks_aws.backends.DatabaseSqsBackend",
        "QUEUES": ["short", "documents"],
        "OPTIONS": {
            "database_alias": "default",
            "workers": WORKER_ROUTES,  # machine-readable route manifest from Terraform
            "allowed_task_modules": ["sample_app.tasks"],
        },
    }
}
```

- `WORKER_ROUTES` is injected by Terraform; application code must not invent a
  conflicting map. Workers validate their own identity against it.
- Tasks declare `@task(queue_name="short")`; producers override per submission
  via `.using(queue_name="documents").enqueue(...)`.
- Public entry points: `DatabaseSqsBackend`, `handlers.worker.lambda_handler`,
  `handlers.sweeper.lambda_handler`, packaged management commands
  (`tasks_worker`, `tasks_sweep`, `tasks_list`, `tasks_show`, `tasks_check`,
  `tasks_dispatch`, `tasks_release`, `tasks_replay`), and packaged admin
  task/attempt/audit views.

## 6. Public extension points

- **Policy catalog** keyed by import path (`policies.py`): argument versions,
  allowed routes, retry classification, ambiguity behavior, and operation-key
  derivation via `operation_key_resolver`.
- **Import-path resolver** (`import_paths.py`) with configurable module allowlist.
- **Control services** (`services/control.py`, including reviewed replay) reused by admin and commands.
- **AdminSite** registration hooks for custom admin sites.

## 7. Requirement ID scheme

Stable IDs are assigned in [`verification-matrix.yaml`](verification-matrix.yaml):

| Prefix | Scope                                            |
| ------ | ------------------------------------------------ |
| `INV`  | Design §1.2 invariants                           |
| `TRN`  | Design §5 state-machine transitions              |
| `ACK`  | Design §9.2 worker acknowledgment outcomes       |
| `FM`   | Design §18.2 failure matrix (table 1)            |
| `DS`   | Design §18.2 deliverable/distribution scenarios  |
| `DLV`  | Design §0 required deliverables                  |
| `WFR`  | Workflow extension requirements (`workflows.rst`) |

IDs are stable across releases; renumbering is a breaking change to the
traceability contract. Matrix ``version: 1`` and ``workflow_version: 1``
register 94 core and 20 optional workflow requirements. Inventory checks validate
assignments; strict candidate checks also require passing, non-skipped evidence
for every registered requirement. The optional app cannot silently drop its gates.

## 8. Failure-matrix fixture scope

The release baseline remains batch size one with zero batch window. FM-027 and
FM-028 use direct service invocations of the deployed worker with multi-record
SQS-shaped events. They exercise partial response identity and process death
between records without changing the supported production event-source mapping.
They do not establish larger-batch ESM deletion behavior or advertise larger
batches as supported. FM-029 uses separate original/compatible images, a retained
import shim, an additive sample index migration, old queued/held work, and rollback.
All three assignments still require actual AWS results before release.

## 9. Workflow extension (optional)

`django_tasks_aws.workflows` is an optional orchestration layer over the core
backend. It is not part of the initial execution contract in section 3; its
versioned contract lives in [`workflows.rst`](workflows.rst) and its requirements
carry the `WFR` prefix in the verification matrix.

- **Scope.** Static graphs whose steps are known at submission time. Dynamic
  expansion, conditional branches, human approvals, and compensation are
  unsupported.
- **Boundary.** Workflows own run/node/dependency records only; task execution,
  leases, retries, and result retention remain the core engine's responsibility
  through the `TaskAdapter` boundary.
- **Support evidence.** Workflow behavior requires verification on the same database matrix
  as the core engine; current local evidence is PostgreSQL-only. The extension shares the release gate: its advertised
  support claims require the same installed and deployed evidence.
