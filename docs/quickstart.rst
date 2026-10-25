===========
Quick start
===========

Follow :doc:`installation` to install the library, configure the Django backend,
and apply its migrations. See :doc:`configuration` for worker routes and runtime
settings, and :doc:`architecture` for the delivery and recovery model.

Runnable example
================

The standalone `django-aws-tasks-example repository
<https://github.com/sharingan-no-kakashi/django-aws-tasks-example>`_ contains a
Django application with a web UI, database containers, local AWS emulation,
Lambda image builds, and deployment tooling. Follow that repository's README
for setup, development, tests, and teardown. Run its commands from its checkout;
the library Makefile provides library maintenance commands only.

The library supplies reusable infrastructure in ``terraform/modules/``. The
consumer owns the ``terraform/bootstrap/`` and ``terraform/application/`` roots
that deploy the application and use those modules.

A minimal task
==============

Tasks are ordinary ``django.tasks`` tasks, decorated with a route:

.. code-block:: python

   from django.tasks import task

   @task(queue_name="short")
   def record_event(event_id: str) -> str:
       ...

Enqueue inside a business transaction:

.. code-block:: python

   from django.db import transaction

   from myapp.models import Event
   from myapp.tasks import record_event

   def create_event(kind: str):
       with transaction.atomic(using="default"):
           event = Event.objects.using("default").create(kind=kind)
           result = record_event.enqueue(event_id=str(event.pk))
       return event, result

The returned :class:`~django.tasks.TaskResult` becomes durable only when the
surrounding transaction commits. See :doc:`architecture` for why.

Make the task executable
========================

Place the task at module scope in ``myapp/tasks.py`` and allow ``myapp.tasks``
in the backend settings. For an application with an ``Event`` model containing
``kind`` and ``processed`` fields, a simple repeatable database task is:

.. code-block:: python

   from django.tasks import task

   from myapp.models import Event

   @task(queue_name="short")
   def record_event(event_id: str) -> dict:
       updated = Event.objects.filter(pk=event_id).update(processed=True)
       return {"event_id": event_id, "found": bool(updated)}

Repeatedly setting this flag has the same business effect. A payment, email, or
other remote action needs its own idempotency design. Pass identifiers and JSON
values into tasks; reload model instances inside the function. A producer's
in-memory objects are not transferred to the worker.

The transaction example above assumes the task backend also uses database alias
``default``. With Django autocommit and no explicit surrounding transaction,
the task insert commits independently of a preceding model write. Wrapping both
writes in ``atomic()`` makes rollback remove both the event and its task.

Execute and inspect
===================

With a real coordination database configured, you can run a bounded local worker
from the consumer project:

.. code-block:: console

   python manage.py tasks_list --queue short --json
   python manage.py tasks_worker --queue short --limit 1
   python manage.py tasks_show TASK_UUID --attempts --json

Replace ``TASK_UUID`` with ``result.id`` returned at enqueue time. The local
worker selects due SQL records and uses the same claim/execution services as
Lambda. It does not consume SQS messages, and it requires a separate process
running on the main thread with Unix timers available. Already-published queue
messages may still arrive later and will be checked against the current state.

In AWS, the queue's event-source mapping invokes the worker instead. The
scheduled sweeper repairs missing publications and expired claims. A healthy
local execution verifies task code and database coordination; AWS delivery,
permissions, and network access require their own deployment checks.

Read the result
===============

The returned object is a snapshot. Refresh it before deciding whether execution
has completed:

.. code-block:: python

   from django.tasks import TaskResultStatus

   result.refresh()
   print(result.id, result.status, result.attempts)
   if result.status == TaskResultStatus.SUCCESSFUL:
       print(result.return_value)

For a later request or process, load the result by its saved ID:

.. code-block:: python

   from django.tasks import task_backends

   result = task_backends["default"].get_result(saved_task_id)

Avoid waiting for completion inside the transaction that enqueues the task:
the worker cannot claim an uncommitted row. Public result access expires after
the configured retention window even though the compact task identity survives.

Submit the same business request safely
=======================================

Use the package's submission helper when a request may be repeated:

.. code-block:: python

   from django_tasks_aws import submit

   result = submit(
       record_event,
       kwargs={"event_id": str(event.pk)},
       key=f"record-event:{event.pk}:v1",
       scope="event-processing",
   )

An identical keyed submission returns the original task. Reusing that key for
different arguments, routing, or other accepted identity fields raises
``SubmissionConflict``. Omitting ``scope`` uses the backend alias. Both key and
scope must be nonempty strings of at most 255 characters when supplied.
The helper keeps submission metadata separate from the task's keyword arguments.

Submission deduplication prevents duplicate acceptance under the same identity;
it does not make a remote effect idempotent. After result expiry, repeating the
accepted key raises ``ExistingTaskResultExpired`` instead of accepting new work.
Use a new identity only when you intend a new business operation.

If nothing runs
===============

Inspect the task before submitting another copy. A future ``available_at`` means
it is scheduled; a hold needs investigation; a running task already has a lease.
If a task is due with no attempt, check route pauses, worker configuration,
publication, and the sweeper. :doc:`operations` explains these categories and
the audited recovery actions.
