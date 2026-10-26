===============
Troubleshooting
===============

Start with configuration and task state
=======================================

Run these commands in the consuming Django project:

.. code-block:: console

   python manage.py check
   python manage.py tasks_workers --backend=default --json
   python manage.py tasks_check --backend=default --json

``--backend`` selects an entry in ``TASKS``. Its database alias is configured
separately in ``OPTIONS['database_alias']``. Confirm migrations are applied to
that database and that all producers, workers, and sweepers use the same schema.

A task stays pending
====================

Inspect eligibility time, hold reason, route, and attempt history. Pending work
may be scheduled for later, waiting for retry, held for review, or due now.
Check that its route has a queue event mapping and that the scheduled sweeper
is enabled. See :doc:`operations` for inspection and recovery commands.

A worker cannot import a task
=============================

Include the task module in the worker artifact and in ``allowed_task_modules``.
Keep task import paths compatible across producer and worker versions. A task
must be a synchronous Django task defined at module scope.

The database or route is rejected
=================================

Install the selected database driver extra in every runtime. Use a writable
primary and the engine settings in :doc:`database-backends`. Route names in
``QUEUES``, task decorators, and the worker manifest must agree; queue identities
must be distinct. See :doc:`configuration` for timing and policy checks.

An external outcome is ambiguous
================================

Look up the business operation in the downstream provider before retrying.
A SQL lease cannot undo an external effect. Use the audited hold-release or
replay procedure in :doc:`operations`, preserving the operation identity and
recording the reason for the decision.
