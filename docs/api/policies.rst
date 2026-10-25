========
Policies
========

What a policy controls
======================

A ``PolicyCatalog`` resolves exact task import paths to ``TaskPolicy`` records,
falling back to a default policy. Overrides inherit the backend's allowed routes
and attempt budget unless explicitly supplied; other policy fields use their
declared defaults. Policies constrain routing, supported payload versions,
retry classification, and operation-key derivation.

``classify_failure()`` compares exact exception class paths. Permanent
classification wins, followed by budget exhaustion, explicit retry classification,
and finally ``retry_safe``. ``classify_ambiguity()`` uses the separate
``ambiguous_retry`` decision and remaining budget. Neither function sends a queue
message: outcome services persist the chosen transition and retry time.

``compute_backoff()`` computes exponential retry delay with jitter. Its ordinary
range starts at 60 seconds and caps at 30 minutes; an explicitly supplied
``retry_after`` can extend the returned delay. Provider exceptions are not
automatically converted to that parameter by the task execution wrapper.

An operation-key resolver receives a list of positional arguments and a copy of
keyword arguments. Return ``None`` or a nonempty string up to 255 characters.
Choose a deterministic business identity shared with the handler's provider
request; a new random value on each submission defeats that purpose.
See :doc:`../configuration` for examples and policy defaults.

Policy reference
================

.. automodule:: django_tasks_aws.policies
   :members:
   :undoc-members:
