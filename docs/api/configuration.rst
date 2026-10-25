=============
Configuration
=============

Loading versus runtime checks
=============================

Configuration loading converts dynamic Django settings into route and policy
records. ``validate_backend()`` checks agreement between those records, queue
names, the task allowlist, and the configured database adapter. It can validate
adapter selection without opening a database connection.

``require_writer()`` is a runtime check against the actual database connection.
``get_invocation_backend()`` selects ``TASK_BACKEND_ALIAS``, validates the backend,
runs an optional credential refresh hook, and requires a writer. Keeping these
stages separate lets startup catch structural mistakes without pretending to
have verified server state or remote permissions.

Load configuration through these boundaries instead of maintaining a second
parser in a custom entry point. See :doc:`../configuration` for defaults, strict
input types, route timing relationships, and runtime environment variables.

Function reference
==================

.. automodule:: django_tasks_aws.configuration
   :members:
