==============================
django-tasks-aws documentation
==============================

**django-tasks-aws** is a serverless Django task system on AWS. It plugs into
Django's built-in :mod:`django.tasks` framework and replaces the ephemeral
in-process queue with a durable, multi-worker pipeline:

* **The SQL database** owns the durable task state — every accepted task, attempt, and
  operator action is a committed row.
* **SQS** standard queues carry delivery signals only, never execution authority.
* **Lambda** workers claim leases, execute the importable task, and finalize
  attempts.
* **A scheduled sweeper** reconciles expired leases, lost publications, and
  unfinished work.

The system is designed for *recoverability*: at-least-once delivery, explicit
ambiguity handling, audited replay/release, and bounded retention are first-class
features rather than afterthoughts.

.. warning::

   **Status: beta.** The backend, coordination services, Lambda adapters,
   and local sample/operator tooling are implemented. AWS deployment and release
   evidence remain incomplete. No AWS deployment is claimed.

.. toctree::
   :maxdepth: 2
   :caption: User guide

   installation
   quickstart
   architecture
   configuration
   workflows
   database-backends
   operations
   release

.. toctree::
   :maxdepth: 2
   :caption: API reference

   api/index

----

Quick reference
===============

.. list-table::
   :header-rows: 1

   * - Concern
     - Value
   * - Distribution name
     - ``django-tasks-aws``
   * - Import namespace
     - ``django_tasks_aws``
   * - Backend class
     - :class:`django_tasks_aws.backends.DatabaseSqsBackend`
   * - Worker entry point
     - :func:`django_tasks_aws.handlers.worker.lambda_handler`
   * - Sweeper entry point
     - :func:`django_tasks_aws.handlers.sweeper.lambda_handler`
   * - Python
     - ``>=3.12,<3.15``
   * - Django
     - ``>=6.1,<7``
   * - Database
     - PostgreSQL 15+ release target; experimental MySQL 8.4+, MariaDB 10.6+,
       Oracle 21c+ adapters (writable primary)

The repository's ``docs/implementation-spec.md`` records the frozen first-release
contract. Development notes and acceptance reports are supporting evidence;
the guides below explain how to use the implemented interfaces.

Where to begin
==============

If you are integrating the backend into an application, read :doc:`installation`
and :doc:`quickstart` first. They explain which components you need and walk
through acceptance, execution, and result inspection. Read :doc:`architecture`
before designing a task that changes an external system: retry safety depends
on what that system can identify and confirm.

Use :doc:`configuration` when connecting your application's settings to its
infrastructure. :doc:`database-backends` explains the database guarantees behind
those settings and distinguishes implemented experimental adapters from the
PostgreSQL release target. :doc:`workflows` adds static dependencies between
ordinary tasks after the basic task path works.

Operators should start with :doc:`operations`, especially the distinction
between releasing a hold and replaying terminal work. Maintainers should use
:doc:`release` to understand which verification evidence is still required.
The :doc:`api/index` supplies function and type details for integrations.

What acceptance guarantees
==========================

A committed task row records the application's intention to perform work. It
does not guarantee that a worker has started, that a provider has accepted a
request, or that an external effect happened once. A queue outage after commit
can delay execution while the accepted work remains discoverable in SQL.
Conversely, a duplicate queue message cannot authorize an extra execution of a
completed task: the worker checks the durable state first.

Plan for three separate questions in your application: was work accepted, what
is its current execution state, and can the business outcome be independently
verified? Task IDs answer the first two; a business operation key and provider
records help answer the third.
