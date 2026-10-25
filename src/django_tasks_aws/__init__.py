"""A serverless Django task system on AWS.

The SQL database owns durable task state; SQS provides delivery signals; Lambda
workers claim leases and finalize attempts; a scheduled sweeper reconciles
unfinished work.

The public entry points are:

- ``django_tasks_aws.backends.DatabaseSqsBackend`` — the Django Tasks backend.
- ``django_tasks_aws.handlers.worker.lambda_handler`` — the SQS Lambda worker.
- ``django_tasks_aws.handlers.sweeper.lambda_handler`` — the scheduled sweeper.
"""

from typing import Any

__version__ = "0.1.0"

__all__ = ["DatabaseSqsBackend", "__version__", "submit"]


def __getattr__(name: str) -> Any:
    if name == "submit":
        from django_tasks_aws.services.enqueue import submit

        return submit
    if name == "DatabaseSqsBackend":
        from django_tasks_aws.backends import DatabaseSqsBackend

        return DatabaseSqsBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
