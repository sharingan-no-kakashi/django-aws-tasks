=============
API reference
=============

The public API is organized by responsibility. Most applications interact with
the backend and the policy catalog directly; the services layer is used by the
Lambda adapters, management commands, and the admin.

Choose an integration boundary
==============================

Application producers normally use Django tasks, the configured backend, and
``django_tasks_aws.submit`` for deduplicated acceptance. Task bodies use ordinary
Python and may opt into execution-budget and progress helpers. Operators should
prefer management commands and admin recovery over low-level mutations.

The service reference explains the protocol for custom adapters and tooling.
Read its transaction and ownership requirements before calling those functions
directly. Model and schema pages describe stored records and validated inputs;
they do not authorize bypassing service transitions.

The optional graph composition, inspection, and recovery interfaces are explained
in :doc:`../workflows`. Use that guide alongside the core result, policy, and
service references because workflow nodes execute through ordinary tasks.

.. toctree::
   :maxdepth: 2

   backends
   policies
   schemas
   models
   exceptions
   handlers
   configuration
   serialization
   import_paths
   results
   sqs
   telemetry
   admin
   services
   commands
