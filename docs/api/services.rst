========
Services
========

The service layer owns the coordination logic shared by producers, workers, the
sweeper, management commands, and the admin. Each module handles one stage of
the task lifecycle.

Function scopes
===============

Service functions use :func:`django_tasks_aws.decorators.scoped` to apply
transactions, telemetry, and execution budgets. Context arguments are resolved
from the current function call. Extracted helpers keep transaction boundaries
separate from post-commit reporting and task execution. Low-level cursor and
file ownership remains explicit inside the scope implementations.

.. automodule:: django_tasks_aws.decorators
   :members: scoped

Enqueue and execution
=====================

Acceptance validates and persists identity before registering post-commit
publication. ``submit()`` is the application helper; lower-level ``enqueue()``
returns an outcome indicating whether it created a row or found an identical
keyed submission. Both remain subject to the caller's outer transaction.

Execution resolves the accepted task, binds its saved arguments and optional
Django context, runs outside coordination locks, and routes the result through
ownership-checked finalization. Budget helpers are valid only within that active
execution scope.

.. automodule:: django_tasks_aws.services.enqueue
   :members:

.. automodule:: django_tasks_aws.services.execution
   :members:

Claims and outcomes
===================

A claim allocates a lease and its attempt together. Finalization must match the
current token and an unexpired lease before changing the task and attempt.
A stale result must not overwrite an outcome owned by a later attempt. Maintain
task-before-attempt lock order when extending these transitions.

.. automodule:: django_tasks_aws.services.claims
   :members:

.. automodule:: django_tasks_aws.services.outcomes
   :members:

Dispatch and delivery
=====================

Dispatch reserves publication work in SQL, sends outside the coordination
transaction, and records the outcome. Repeated signals are expected under
uncertainty. Publication limits count delivery work independently of the
execution attempts allocated by claims.

.. automodule:: django_tasks_aws.services.dispatch
   :members:

Database contexts and queries
=============================

Database contexts scope atomic coordination and adapter-specific limits.
Query helpers provide the correct database-clock expressions for the connection
compiling the query. Use these boundaries rather than hard-coding a server's
clock or session timeout in a shared service.

.. automodule:: django_tasks_aws.databases.context
   :members:

.. automodule:: django_tasks_aws.databases.queries
   :members:

Reconciliation
==============

Reconciliation visits bounded batches of expired or unfinished work and repairs
progress from durable evidence. When enabled, workflows advance after task
recovery and before dispatch. Deadlines limit how much one invocation attempts;
remaining work is left for later passes rather than requiring a complete scan.
The operator sweep wrapper adds audited control semantics and dry-run previews.

.. automodule:: django_tasks_aws.services.reconcile
   :members:

.. automodule:: django_tasks_aws.services.operator_sweep
   :members:

Worker
======

The worker validates event origin and backend configuration, parses an envelope,
checks compatibility, and attempts a claim before executing. A received message
can legitimately lead to no execution when work is terminal, held, not yet due,
or owned elsewhere. Inspect its decision together with SQL state.

.. automodule:: django_tasks_aws.services.worker
   :members:

Inspection and control
======================

Inspection reads bounded, scoped snapshots without repairing or publishing work.
Control accepts explicit actor, reason, and request identity, checks eligibility,
and persists an audit outcome. Invariant checks identify inconsistent history;
they do not infer missing business outcomes or silently rewrite attempts.

.. automodule:: django_tasks_aws.services.inspection
   :members:

.. automodule:: django_tasks_aws.services.control
   :members:

.. automodule:: django_tasks_aws.services.invariants
   :members:

Retention
=========

Retention redacts eligible terminal details in bounded locked batches. It
preserves compact task identity, deduplication and control identities, and replay
lineage. Active workflows protect required inputs/results until their own detail
retention permits redaction. See :doc:`../operations` before changing windows.

.. automodule:: django_tasks_aws.services.retention
   :members:
