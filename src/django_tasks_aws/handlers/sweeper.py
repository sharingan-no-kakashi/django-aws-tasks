"""Scheduled sweeper Lambda entry point.

A thin adapter: initialize Django once at cold start before importing the
reconciliation service. Django owns connections across warm invocations.
X-Ray tracing and Lambda context injection are provided by AWS Lambda Powertools.
"""

import time
from typing import Any

import django

from django_tasks_aws import telemetry
from django_tasks_aws.telemetry import tracer

django.setup()


@tracer.capture_lambda_handler
@telemetry.event_logger.inject_lambda_context(log_event=False, clear_state=True)
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from django_tasks_aws.services.reconcile import DEFAULT_WORK_SECONDS, run_sweeper

    seconds = DEFAULT_WORK_SECONDS
    if context is not None:
        seconds = min(seconds, max(0, context.get_remaining_time_in_millis() / 1000 - 5))

    return run_sweeper(deadline=time.monotonic() + seconds)
