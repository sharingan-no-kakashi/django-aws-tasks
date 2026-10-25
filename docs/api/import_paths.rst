============
Import paths
============

Paths are durable application identity
======================================

A stored path such as ``myapp.tasks.record_event`` must resolve to a module-level
Django ``Task`` in the worker's own artifact. Local functions, closures, and
renamed or removed symbols cannot provide that durable lookup contract.
The module allowlist matches a named module and its dotted descendants, rather
than arbitrary string prefixes.

``resolve_task()`` performs the import and type check. The backend requires an
explicit nonempty allowlist even though the low-level helper can be called with
an empty one. Keep the application boundary's restriction in custom integrations.

``reconstruct_record_task()`` binds the imported task to its saved backend and
queue without modifying the global task object. It checks the saved
``takes_context`` contract so a changed decorator cannot silently alter how
accepted arguments are passed. Keep import aliases and compatible signatures
available while old tasks and waiting workflow nodes still need them.

Function reference
==================

.. automodule:: django_tasks_aws.import_paths
   :members:
