=============
Serialization
=============

Supported values and size limits
================================

Task values use JSON primitives: null, booleans, strings, integers, finite
floats, lists, and dictionaries with string keys. Convert UUIDs, datetimes,
Decimals, tuples nested within input, model instances, and custom classes into
an explicit application representation before submitting them. Positional
arguments themselves are normalized into the payload's outer list.

The complete argument envelope has a 32 KiB UTF-8 limit; a result has a 64 KiB
limit. These limits apply to encoded bytes, so a Python character count is not
an adequate size check. Store large documents externally and pass stable keys.
Results undergo the same safe-value validation before durable storage.

Canonical encoding uses sorted keys and compact separators. Payload hashing
combines task path, payload version, and canonical arguments. Submission identity
adds routing and other accepted task options in the enqueue service; changing
those can conflict even when the payload hash is unchanged.

The queue envelope contains ``schema_version`` and ``task_id`` only. Parsing it
does not authorize task execution; claims must still consult SQL. Unknown schema
versions and malformed bodies raise ``SerializationError``.

Function reference
==================

.. automodule:: django_tasks_aws.serialization
   :members:
   :undoc-members:
