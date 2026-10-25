=============
Configuration
=============

The backend is configured through ``TASKS`` in Django settings. Configuration
is loaded and validated at the application boundary, then passed into the domain
services as immutable typed records (see :mod:`django_tasks_aws.schemas`).

Example
=======

.. code-block:: python

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

See :doc:`database-backends` for engine setup and adapter overrides.

Backend options
===============

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Option
     - Default
     - Purpose
   * - ``database_alias``
     - ``default``
     - Writable SQL database connection for atomic acceptance and coordination.
   * - ``workers``
     - Required
     - Trusted route manifest; names must exactly match ``QUEUES``.
   * - ``allowed_task_modules``
     - Required
     - Explicit import module/prefix allowlist for resolvable tasks.
   * - ``max_attempts``
     - ``5``
     - Durable claim budget for newly accepted work.
   * - ``policies``
     - Empty
     - Per-import-path task policy overrides.
   * - ``immediate_dispatch``
     - ``True``
     - Best-effort publication after acceptance commits.
   * - ``paused_routes``
     - Empty
     - Prevent new claims/publications on selected routes.
   * - ``producer_version``
     - ``TASK_APPLICATION_VERSION``, then package version
     - Application release that created the work.
   * - ``credentials_resolver``
     - ``None``
     - Optional importable ``(database_alias) -> None`` credential refresh hook.
   * - ``result_retention_days``
     - ``30``
     - Public result/replay window.
   * - ``failure_diagnostic_retention_days``
     - ``90``
     - Failed attempt/error detail retention.
   * - ``audit_detail_retention_days``
     - ``90``
     - Actor/reason detail retention.
   * - ``overdue_grace_seconds``
     - ``300``
     - Inspection threshold for overdue runnable work.

Route manifest
==============

``WORKER_ROUTES`` is the ``route_manifest`` Terraform output, decoded as a
mapping of route name to worker profile. Every route requires:

* ``source_queue_arn`` and ``source_queue_url`` — the worker's standard SQS queue.
* ``timeout_seconds`` — the Lambda timeout; must exceed the five-second
  finalization allowance.
* ``lease_seconds`` — the fixed lease; must cover the timeout plus five seconds.
* ``visibility_timeout_seconds`` — at least six times the timeout.
* ``max_receive_count`` — between 5 and 1000.
* ``concurrency`` and ``dispatch_limit`` — positive integers.
* ``redispatch_seconds`` — non-negative.

Sources must be distinct standard queues, and the URL's account/name must match
the ARN. The Terraform module validates the same bounds for the deployed
resources. See :class:`django_tasks_aws.schemas.WorkerConfig`.

Task policies
=============

Policies are the declarative extension point, keyed by import path. Each policy
accepts:

* ``allowed_routes`` — routes this task may use.
* ``payload_versions`` — supported argument envelope versions (initially ``(1,)``).
* ``max_attempts`` — per-task execution budget.
* ``retryable_exception_paths`` and ``permanent_exception_paths`` — declared
  exception classes that always retry or never retry.
* ``retry_safe`` — retry unexpected failures when budget remains.
* ``ambiguous_retry`` — retry (rather than hold) an ambiguous external outcome.
* ``operation_key_resolver`` — import path of a ``(args, kwargs) -> str | None``
  function deriving the business operation identity.

Failure classification follows
:func:`django_tasks_aws.policies.classify_failure`; ambiguity follows
:func:`django_tasks_aws.policies.classify_ambiguity`. See
:mod:`django_tasks_aws.policies` for the full API.

Execution budget
================

Task bodies can protect downstream work against the invocation deadline:

.. code-block:: python

   from django_tasks_aws.services.execution import get_remaining_seconds, require_time

   require_time(3)
   timeout = min(10, get_remaining_seconds())

The allowance excludes five seconds reserved for finalization. The worker
refreshes the Lambda context budget around execution, and ``tasks_worker``
applies a hard Unix process timer on the main thread. A timed-out command
terminates without Python cleanup; reconciliation closes its abandoned claim.
See :mod:`django_tasks_aws.services.execution`.

Validation
==========

Unsafe or incomplete configuration fails loudly. The package registers Django
system checks (``django_tasks_aws.E001`` through ``E005``) that run at startup,
and :func:`django_tasks_aws.configuration.validate_backend` re-validates at the
invocation entry point. Configuration errors raise
:class:`django_tasks_aws.exceptions.ConfigurationError`.

Worker profiles and policy overrides use strict Pydantic validation. Supply
actual booleans and integers: strings such as ``"false"`` and ``"5"``, and
booleans in integer fields, are rejected. Unknown profile fields are rejected
to catch misspelled options. Policy sequences accept lists or tuples while
validating each element without coercion. These inputs are converted into the
existing immutable domain records after validation.

Build a complete route profile
==============================

For illustration, a single ``short`` route can be represented as:

.. code-block:: python

   WORKER_ROUTES = {
       "short": {
           "source_queue_arn": "arn:aws:sqs:eu-west-1:123456789012:app-short",
           "source_queue_url": "https://sqs.eu-west-1.amazonaws.com/123456789012/app-short",
           "timeout_seconds": 30,
           "lease_seconds": 35,
           "visibility_timeout_seconds": 180,
           "max_receive_count": 5,
           "concurrency": 2,
           "dispatch_limit": 10,
           "redispatch_seconds": 60,
           "paused": False,
       },
   }

This is a shape example; replace the resource identities with deployed outputs
and set ``QUEUES`` to ``["short"]`` for this one-route configuration. Changing
this dictionary alone does not update AWS resources. Keep actual Lambda timeout,
queue visibility, concurrency, and event mappings aligned with Terraform.

With these values, the invocation has 30 seconds, the lease covers 35 seconds,
and the queue hides a delivered message for 180 seconds. The five-second
finalization allowance leaves less than 25 seconds for business work after
startup and coordination. Size routes for worst-case work and downstream timeouts,
rather than assuming all of the Lambda timeout is available to a task body.

``dispatch_limit`` bounds unclaimed delivery attempts independently of
``max_attempts``. ``redispatch_seconds`` controls when another delivery signal
may be attempted. Neither setting grants permission to repeat a business effect.
A paused route suppresses new claims and publications; it does not revoke an
already-running attempt.

Understand retry defaults
=========================

The default policy has ``retry_safe=False`` and ``ambiguous_retry=False``.
Setting ``max_attempts=5`` therefore permits up to five claims; it does not make
all errors retryable. Ordinary failures are classified in this order:

#. An exact class path in ``permanent_exception_paths`` fails permanently.
#. No remaining attempts means terminal failure.
#. An exact class path in ``retryable_exception_paths`` permits a retry.
#. Otherwise, ``retry_safe`` decides whether the failure retries or fails.

Class paths are compared exactly, so listing a base class does not implicitly
match every subclass. ``PermanentFailure`` raised by the task explicitly takes
the terminal path. ``AmbiguousOutcome`` takes the ambiguity path, which retries
only with budget remaining and ``ambiguous_retry=True``; otherwise it holds,
or fails if the budget is exhausted. A result serialization failure after the
body ran is also treated as ambiguous because an effect may already exist.

For a repeatable task, a policy might include:

.. code-block:: python

   "policies": {
       "myapp.tasks.record_event": {
           "allowed_routes": ["short"],
           "max_attempts": 3,
           "retry_safe": True,
           "ambiguous_retry": True,
       },
   }

Enable these flags only after deciding how repeated execution affects the
business operation. An operation key is useful evidence but supplies no remote
protection unless the handler uses it in the provider's idempotency protocol.
The accepted record stores its attempt budget; later policy changes cannot
increase that stored budget through normal retry classification.

Runtime environment
===================

``DJANGO_SETTINGS_MODULE`` selects the consuming project's settings before the
Lambda handler imports. ``TASK_BACKEND_ALIAS`` selects the ``TASKS`` entry for
invocations and defaults to ``default``. ``TASK_APPLICATION_VERSION`` supplies
the producer version unless explicitly overridden in backend options.
``DJANGO_TASKS_AWS_ENVIRONMENT`` labels telemetry for the deployed environment.
Workers additionally require ``TASK_WORKER_NAME`` and
``TASK_WORKER_SOURCE_ARN``. They must identify a route and its source queue ARN
in the manifest; a mismatch is rejected before claiming. These values serve
different purposes and should be injected consistently into consumer-owned
artifacts.

A credentials resolver runs when the invocation backend is selected, before the
writer check. It receives the SQL database alias and must update credentials and
close a cached connection when those credentials change. Do not serialize
credentials into task arguments or the route manifest.

Separate latency from durability
================================

``immediate_dispatch=True`` publishes after commit to reduce startup latency.
Disabling it leaves accepted rows for reconciliation, which can be useful when
publication must be moved off a request path. In either mode, operate a scheduled
sweeper. A successful enqueue reports database acceptance, not successful queue
delivery or execution.

Retention options determine how long result consumers, incident investigations,
and replays can use detailed data. Set them together with workflow retention
when workflows are enabled; active workflows may need parent results beyond an
ordinary task's standalone result window. See :doc:`operations` and
:doc:`workflows` before shortening retention.
