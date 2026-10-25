"""SQS Lambda worker entry point.

A thin adapter: initialize Django once at cold start before importing the
model-dependent worker service. Django owns connections across warm invocations.
AWS Lambda Powertools provides X-Ray tracing and Lambda context injection.
"""

from typing import Any

import django

from django_tasks_aws import telemetry
from django_tasks_aws.telemetry import tracer

django.setup()


@tracer.capture_lambda_handler
@telemetry.event_logger.inject_lambda_context(log_event=False, clear_state=True)
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from django_tasks_aws.services.worker import process_event

    return process_event(event, context)
