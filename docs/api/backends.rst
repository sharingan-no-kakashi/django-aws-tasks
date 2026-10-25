=======
Backend
=======

``DatabaseSqsBackend`` supports PostgreSQL, MySQL, MariaDB, and Oracle.
Engine-specific operations implement
:class:`django_tasks_aws.databases.base.DatabaseAdapter`; see
:doc:`../database-backends`.

Application entry points
========================

Configure the backend through Django's ``TASKS`` setting and use ordinary
``@task`` definitions to enqueue work. The backend supports delayed eligibility
and result retrieval, but does not support task priority or async task bodies.
Application code normally needs the task object and its result, rather than
calling coordination services directly.

``enqueue()`` validates task identity, route policy, and JSON arguments before
accepting a record. Publication is registered for after commit. ``get_result()``
loads a result within this backend's alias and retention window, resolving its
stored task path from the current deployment. A result ID from a different
backend is not visible through this backend.

For keyed acceptance, use ``django_tasks_aws.submit`` as shown in
:doc:`../quickstart`. To change a task's destination, use Django's
``task.using(backend=..., queue_name=...)`` before submitting it; do not edit an
accepted task's route in SQL.

Class reference
===============

.. automodule:: django_tasks_aws.backends
   :members:
   :exclude-members: enqueue, get_result, validate_task
