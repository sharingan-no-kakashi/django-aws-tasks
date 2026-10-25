==========
Operations
==========

This guide covers inspection, recovery, retention, telemetry, and incident
response. Operator commands, admin permissions, and incident procedures are
documented here; the raw source document is ``docs/operations.md``.

.. _operations-migrations:

Migrations
==========

Add the package to ``INSTALLED_APPS`` and run ``python manage.py migrate``.
The core and optional workflow apps each have one ``0001_initial`` migration
that creates their complete current schema. This pre-release supports fresh
installations only. Recreate disposable development databases that used an
earlier migration chain; no upgrade or fake-migration procedure is provided.

Inspect before acting
=====================

.. code-block:: console

   python manage.py tasks_list --problematic --queue documents --limit 50 --json
   python manage.py tasks_show TASK_UUID --attempts --json
   python manage.py tasks_workers --json
   python manage.py tasks_check --fail-on-problems --json

Use ``--backend NAME`` to select a configured backend. Inspection commands never
publish or repair work. ``tasks_show --sensitive`` includes arguments, results,
and retained error text.

``tasks_list`` supports ``--queue``, ``--category``, ``--status``,
``--task-name``, ``--hold-reason``, ``--search``, ``--limit`` (1–200), and
``--offset`` (0–100000). The categories are ``scheduled``, ``retrying``, ``due``,
``overdue``, ``reserved``, ``running``, ``expired``, ``held``, ``exhausted``,
``successful``, and ``failed``. ``due`` means runnable; ``overdue`` additionally
requires execution eligibility older than ``overdue_grace_seconds``.

Release, replay, dispatch, and recovery
=======================================

Every control request needs an actor, a reason, and a stable request identity.
The CLI generates a request ID when omitted and prints it to stderr **before**
requesting changes.

.. code-block:: console

   python manage.py tasks_release TASK_UUID --reason "Worker repaired" \
     --request-id repair-2026-10-18 --reset-delivery --dry-run --json
   python manage.py tasks_replay TASK_UUID --reason "Provider outcome investigated" \
     --request-id replay-2026-10-18 --effects-reviewed --dry-run --json
   python manage.py tasks_dispatch TASK_UUID --reason "Publication repaired" --json
   python manage.py tasks_recover TASK_UUID --reason "Expired invocation investigated" --json
   python manage.py tasks_sweep --queue documents --limit 100 --dry-run --json

Recovery commands accept up to 200 target IDs and report each result.
``--dry-run`` never changes records, writes audit events, or sends queue
messages. An identical request/action/target/actor/reason returns its retained
result; changed intent with the same request identity conflicts.

Release preserves the execution budget; ``--reset-delivery`` resets only the
unclaimed-publication budget. Ambiguous-effect holds additionally require
``--effects-reviewed``. Replay leaves the terminal original unchanged, creates
one linked replacement, and requires explicit effect review even for a
successful original. Replay cannot create a new intended effect or modify
arguments.

.. _operations-operation-key:

The business operation key
==========================

The operation key is derived at enqueue time and persisted on the record. A task
policy declares ``operation_key_resolver`` as the import path of a
``(args, kwargs) -> str | None`` function that returns the same identity the
handler uses. The key must be a non-empty string of at most 255 characters.
Derived keys populate search and survive replay lineage; tasks without a
resolver leave the column null and fall back to handler-derived identity.

Operation keys identify the intended business effect and survive replay; they do
not provide remote idempotency by themselves.

Admin roles and review pages
============================

With ``django.contrib.admin`` enabled, the package registers tasks, attempts,
and control events. A read-only staff role needs ``view_taskrecord``; recovery
additionally needs ``control_taskrecord``. Sensitive arguments/results/error
text require the separate ``view_task_payload`` permission. These are
deployment-wide model permissions; they do not implement tenant isolation.

All protocol model add/change/delete forms are disabled. Recovery uses a reason
form, a concrete per-target preview, and a signed confirmation valid for 15
minutes. Custom admin sites can call
:func:`django_tasks_aws.admin.register_admin`.

Retention
=========

Configure these ``OPTIONS`` values: ``result_retention_days`` (30),
``failure_diagnostic_retention_days`` (90), ``audit_detail_retention_days`` (90),
``overdue_grace_seconds`` (300), and ``paused_routes`` (``[]``).

.. code-block:: console

   python manage.py tasks_cleanup --limit 100 --dry-run --json
   python manage.py tasks_cleanup --limit 100 --json

Cleanup uses bounded, skip-locked batches and never deletes pending/running/held
work, compact task identities, submission hashes/keys, replay links, or control
identities. After the result window, ``get_result()`` raises
``TaskResultDoesNotExist``, and a repeated accepted submission key raises
:class:`django_tasks_aws.exceptions.ExistingTaskResultExpired`.

Telemetry
=========

Structured *events* are emitted through AWS Lambda Powertools' ``Logger``
(service ``django-tasks-aws``) as JSON on stdout; Powertools injects Lambda
function context and cold-start markers through ``inject_lambda_context``.
Powertools ``single_metric`` writes embedded metric format (EMF) JSON directly
to stdout (namespace ``DjangoTasksAws``); no metrics logger configuration is
required. Set ``DJANGO_TASKS_AWS_ENVIRONMENT`` to
the environment passed to monitoring.

Events correlate task, attempt, route, request, and invocation identities while
omitting arguments, results, and arbitrary exception messages. Application
metrics are registered after commit. Every completed sweep emits ``Heartbeat``,
``OldestDueAge``, ``OldestHoldAge``, ``HeldBacklog``, and ``ExpiredLeases``.
See :mod:`django_tasks_aws.telemetry`.

Pause/resume a route
====================

For a route incident:

#. Inspect its tasks, attempts, invocation logs, queue age, and DLQ.
#. Disable that route's event-source mapping, and deploy its paused manifest
   (or ``OPTIONS["paused_routes"]``) to producers, workers, and sweeper.
#. Confirm the previous worker version has drained before declaring the fleet
   fully paused.
#. Repair configuration/provider/database capacity. Inspect holds; do not reset
   execution counters or rewrite arguments.
#. Resume consistent route configuration and its event mapping; release resolved
   holds in bounded groups.
#. Observe outcome/progress metrics and confirm alerts clear.

Corrupt history
===============

``tasks_check --json`` reports bounded ``invariant_violations``. Pause the
affected route, retain evidence, and record the investigation through the
audited review action:

.. code-block:: console

   python manage.py tasks_review_invariant TASK_UUID \
     --reason "Incident INC-123 evidence retained" --request-id INC-123-review --json

Reconstruct history only from verifiable records. Never manufacture an attempt
or clear ownership merely to make a check green.

Interpret a backlog before recovery
===================================

Queue depth alone cannot describe task health. A queue can be empty while SQL
contains unpublished due work, or contain duplicate deliveries for already
finished tasks. Inspect durable categories alongside queue and invocation data:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Observation
     - Likely meaning
     - First check
   * - Scheduled
     - Work is not yet eligible.
     - Confirm its intended schedule before intervening.
   * - Retrying
     - A prior attempt scheduled a future execution.
     - Inspect attempt errors and retry eligibility time.
   * - Overdue
     - Runnable work has exceeded the configured grace period.
     - Check route pause, publication, capacity, and sweeper health.
   * - Expired
     - A running lease no longer authorizes its owner.
     - Inspect the attempt and reconciliation evidence.
   * - Held
     - Automatic progress requires review or a compatibility repair.
     - Read the hold reason and investigate external effects.
   * - Exhausted
     - A configured budget has been consumed.
     - Distinguish delivery exhaustion from execution exhaustion.

Categories are operational views of durable fields, rather than extra task
statuses. A successful inspection request establishes visibility, not overall
health. Use ``tasks_check --fail-on-problems`` when automation needs a failing
exit status for detected problems.

Choose the smallest eligible action
===================================

``dispatch`` asks for another delivery of existing work; it does not bypass
claim validation. ``recover`` reconciles an expired attempt. ``release`` removes
an eligible hold or resets the delivery budget while preserving the original
execution budget. ``replay`` creates a linked replacement for terminal work
without editing its original arguments or history.

Preview changes using ``--dry-run`` and retain the target IDs, actor, reason,
and request ID with the incident record. Reuse the same request ID if the
command's response is lost and you need to retry the same intent. Use a new ID
for a genuinely new decision. Eligibility is checked again when applying the
action, so a successful preview is not a reservation against concurrent changes.

For an ambiguous provider outcome, first query the provider using the business
operation identity. Record whether the effect succeeded, failed, or remains
unknown. ``--effects-reviewed`` records an operator decision; it cannot prove
that the provider did or did not perform the effect.

Operate the sweeper and cleanup separately
==========================================

Reconciliation restores progress; cleanup expires retained detail. Schedule both
as appropriate, with workflow cleanup as well when that app is enabled. Run
bounded batches repeatedly rather than trying to process an unbounded backlog
in a single invocation. Monitor the sweeper heartbeat and oldest due age together:
a heartbeat proves the process ran, while persistent due age can reveal that
capacity or a particular task still prevents progress.

Cleanup redacts eligible terminal detail and preserves compact identities. A
missing public result after retention therefore does not imply that the original
business request may be safely submitted again. Resolve expired keyed submissions
through application records and provider evidence rather than selecting a new
key merely to bypass the retained identity.

Progress reporting from task bodies
===================================

A task can publish bounded advisory progress during an active claim:

.. code-block:: python

   from django_tasks_aws.progress import report_progress

   report_progress(stage="processing", completed=5, total=20, unit="items")

The helper returns ``False`` if an update is throttled or ownership is stale.
It never renews a lease, completes work, or changes the execution deadline.
``TASKS_AWS_PROGRESS_MIN_INTERVAL_SECONDS`` defaults to one second and accepts
values from 0.1 through 60. Progress stages are limited to 80 characters, units
to 32, and messages to 500; use non-sensitive text suitable for inspection.
A task must not depend on every advisory update being persisted.

Retain useful incident evidence
===============================

Capture task and attempt IDs, backend and route, producer/worker versions,
invocation request IDs, relevant timestamps, hold reasons, and control request
IDs. Correlate them with structured events and provider operation records.
Payload access requires its separate permission; routine logs intentionally
omit arguments and results. Keep sensitive evidence in the application's
approved incident storage rather than adding it to recovery reasons or progress
messages.
