=========
Telemetry
=========

Powertools-backed event output, metrics, masking, and scoped correlation context.
Task lifecycle reporting lives in the services; CloudWatch links are exposed by
:mod:`django_tasks_aws.services.inspection`.

Events, metrics, and context
============================

Events describe individual transitions and correlate task, attempt, route,
backend, invocation, and operator request identities. Metrics aggregate behavior
for alarms and trends. Correlation context attaches useful identifiers to
reporting within a scope; it does not change task state or execution ownership.

The package uses structured stdout logging and embedded metric format rather
than making an application-side CloudWatch metrics request for each event.
Configure log retention and alarm routing in the deployment. The sweeper's
heartbeat and age/backlog metrics are useful together: a functioning invocation
can still leave a persistent backlog when downstream capacity is insufficient.

Lifecycle metrics are registered after commit so rolled-back transitions do
not report successful durable changes. Logs omit task arguments, results, and
arbitrary exception text. Keep custom task logs and progress messages equally
careful; masking helpers cannot make unrestricted application logging safe.
See :doc:`../operations` for incident correlation and environment labels.

Telemetry reference
===================

.. automodule:: django_tasks_aws.telemetry
   :members:
   :undoc-members:
