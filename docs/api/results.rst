=======
Results
=======

Snapshots and refresh
=====================

``DurableTaskResult`` adapts a durable row to Django's task result interface.
It exposes the persisted claim count as both ``attempts_started`` and
``attempts``. Delivery attempts are not included in that count.

An enqueue result describes state at acceptance. ``refresh()`` reloads through
the task's backend; ``arefresh()`` provides the asynchronous refresh interface
without making the task body itself async. A successful refresh updates the
standard Django result fields and the durable attempt count.

Only successful results carry a return value. Errors are assembled from retained
attempt history, falling back to the task's last error summary when necessary.
``get_result()`` and refresh raise Django's ``TaskResultDoesNotExist`` once the
public result window expires or the ID is outside the selected backend.
A compact retained identity is not a promise of indefinitely available payloads.

Result reference
================

.. automodule:: django_tasks_aws.results
   :members:
