===================
Management commands
===================

The package ships operator commands for inspection, recovery, and local
execution. Commands accept ``--backend NAME`` (default ``default``). Operator commands
support ``--json``; ``tasks_worker`` reports text output.

Inspection
==========

* ``tasks_list`` — inspect task summaries without changing state.
* ``tasks_show`` — inspect a task, its attempts, control audit, and replay lineage.
* ``tasks_check`` — read-only configuration and problem-backlog checks.
* ``tasks_workers`` — inspect route configuration and backlog categories.

Recovery
========

* ``tasks_dispatch`` — request audited task dispatch.
* ``tasks_release`` — request audited task release (hold or delivery reset).
* ``tasks_replay`` — request audited terminal replay.
* ``tasks_recover`` — recover one expired attempt through the audited service.
* ``tasks_review_invariant`` — audit review of a corrupt task without guessing a repair.
* ``tasks_sweep`` — run bounded audited reconciliation (or preview with ``--dry-run``).

Maintenance
===========

* ``tasks_cleanup`` — redact expired terminal details without deleting identities.
* ``tasks_worker`` — execute bounded local work for a named backend and route.

Practical command usage
=======================

Run commands from the consumer project's ``manage.py`` with the same settings,
application code, driver, and database credentials used by the deployment.
Backend selection uses the ``TASKS`` key; it is not Django's SQL database alias.
For each command's complete flags, run ``python manage.py COMMAND --help``.

Start with ``tasks_list`` and ``tasks_show --attempts``. Add ``--sensitive`` to
``tasks_show`` only when payload access is needed. JSON output helps scripts
retain structured findings without parsing human-readable tables.

Mutation commands require ``--reason`` and accept a stable ``--request-id``.
Use ``--dry-run`` to preview eligible changes; then apply the reviewed action.
If a response is lost, repeat the same request identity and intent. Supplying
changed arguments under that identity conflicts instead of silently recording
a different action. See :doc:`../operations` for release versus replay.

``tasks_worker --queue short --limit 1`` performs one bounded local selection
and execution pass; it is not a long-running SQS consumer. ``tasks_sweep``
reconciles existing durable work, and ``tasks_cleanup`` expires retained details.
Those operations have different purposes and should be scheduled accordingly.
Optional workflow commands are described in :doc:`../workflows` and require the
workflow app to be installed.

Shared command machinery
========================

.. automodule:: django_tasks_aws.management.operator
   :members:
