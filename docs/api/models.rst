======
Models
======

The durable record model. The enums and models below are the source of truth for
the database invariants described in :doc:`../architecture`.

Read models, mutate through services
====================================

``TaskRecord`` stores accepted identity, JSON input, eligibility, attempt budget,
current lease, hold metadata, and terminal result. ``TaskAttempt`` records each
successful claim and its execution history. Control records retain operator
request identity and outcomes so an interrupted command can be repeated safely.

These rows form one protocol. Updating a status without its lease fields,
creating an attempt without incrementing its parent budget, or deleting an
identity can violate guarantees even if a particular SQL update succeeds.
Use the service layer or operator commands for transitions; direct ORM reads
are appropriate for diagnostics within the application's database scope.

A pending row may be scheduled, retrying, held, or immediately runnable. Consult
eligibility and hold fields alongside status. Public results translate pending
state to Django's ``READY`` status; attempts and inspection categories provide
additional execution detail. See :doc:`../architecture` for the invariants and
:doc:`../operations` for retention of compact identities.

Model reference
===============

.. automodule:: django_tasks_aws.models
   :members:
   :undoc-members:
