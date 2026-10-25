=======
Schemas
=======

Configuration and wire records
==============================

``WorkerConfig`` is a validated route profile, ``WorkerIdentity`` describes the
receiving worker, and ``Envelope`` describes the queue's delivery hint.
``RouteManifest`` maps route names to worker configurations; it is not itself an
AWS resource provisioning API.

Use ``load_route_manifest()`` for the complete manifest. Besides individual
profile bounds, it checks distinct queue identities, standard queue names,
HTTPS URLs, and ARN/URL account-name agreement. Constructing one dataclass does
not substitute for validation of the entire mapping.

The profile input rejects unknown keys and coerced scalar types. For example,
``30`` is an integer timeout while ``"30"`` is a configuration error. The
five-second finalization margin and six-times visibility relationship are
validated before execution. See :doc:`../configuration` for a full profile.

Type reference
==============

.. automodule:: django_tasks_aws.schemas
   :members:
