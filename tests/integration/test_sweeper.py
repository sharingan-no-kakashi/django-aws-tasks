"""Scheduled sweeper handler emits a summary heartbeat."""

from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


def test_sweeper_handler_reports_idle_system():
    from django_tasks_aws.handlers.sweeper import lambda_handler

    context = SimpleNamespace(
        function_name="sweeper",
        memory_limit_in_mb=128,
        invoked_function_arn="arn:aws:lambda:eu-west-1:123456789012:function:sweeper",
        aws_request_id="test-request",
        get_remaining_time_in_millis=lambda: 60_000,
    )
    result = lambda_handler({}, context)

    assert result["recovered"] == 0
    assert result["held"] == 0
    assert result["dispatched"] == 0
    assert result["oldest_due_age_seconds"] is None
