========
Handlers
========

Public Lambda entry points. These thin adapters initialize Django at module
import during cold start, before importing model-dependent services. Django's
connection registry persists across warm invocations. Configure
``CONN_MAX_AGE`` and ``CONN_HEALTH_CHECKS`` as described in :doc:`../installation`.

Deployment contract
===================

Set ``DJANGO_SETTINGS_MODULE`` before importing either handler. Deploy the
consumer application, task definitions, database driver, and settings together
with this package. ``TASK_BACKEND_ALIAS`` chooses the backend used by the
invocation; each worker must also have the route identity expected by the
worker service and the deployed queue mapping.

The worker handler receives an SQS event and returns the worker service's batch
response. The sweeper handler receives a scheduled invocation and passes a
bounded monotonic deadline to reconciliation, reserving five seconds from the
remaining Lambda budget. Neither handler is an HTTP application endpoint.

For local execution use ``tasks_worker`` and for explicit reconciliation use
``tasks_sweep``. These adapters share domain services; importing a Lambda module
into a web view is unnecessary and initializes Django at import time.

Entry-point reference
=====================

.. automodule:: django_tasks_aws.handlers.worker
   :members:

.. automodule:: django_tasks_aws.handlers.sweeper
   :members:
