"""Opt-in consumer helpers over the real durable protocol, without AWS access.

Use a disposable supported SQL database and committed transactions. These helpers
never manufacture terminal states or replace the task's business body.
"""

from .harness import TaskHarness

__all__ = ["TaskHarness"]
