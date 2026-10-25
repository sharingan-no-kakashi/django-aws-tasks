===
SQS
===

Transport responsibility
========================

The transport publishes versioned task-ID envelopes to queues selected from the
trusted route manifest. It does not serialize a Python callable, transport task
arguments, or decide whether an execution is still eligible. That decision
belongs to the claim service reading durable SQL state.

A send can fail before delivery, or appear to fail after SQS accepted it.
Reconciliation and duplicate-safe claim checks handle these publication windows.
Do not turn a send failure after a committed acceptance into a second business
submission with a new identity.

Keep queue credentials and endpoints in runtime configuration. Database adapters
and retry policies remain separate from the transport. Tests can substitute a
transport to exercise publication behavior, but real queue permissions and
Lambda delivery need deployment acceptance. See :doc:`../architecture`.

Transport reference
===================

.. automodule:: django_tasks_aws.sqs
   :members:
