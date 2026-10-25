import json
import logging

import pytest

from django_tasks_aws import telemetry
from django_tasks_aws.services import inspection


def event_records(caplog):
    return [
        record.msg
        for record in caplog.records
        if record.name == "django-tasks-aws" and isinstance(record.msg, dict)
    ]


def metric_records(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_event_redacts_unapproved_fields_and_bounds_size(caplog):
    with caplog.at_level(logging.INFO):
        telemetry.log_event(
            "task.failed",
            task_id="id",
            error_type="x" * 10000,
            payload={"password": "secret"},
            message="token=secret",
        )
    record = event_records(caplog)[0]
    assert "secret" not in json.dumps(record)
    assert record["task_id"] == "id" and len(record["error_type"]) == 256
    assert len(json.dumps(record)) < 4096


def test_metrics_have_only_bounded_dimensions(capsys):
    telemetry.emit_metric("Failed", queue="short")
    record = metric_records(capsys)[0]
    definition = record["_aws"]["CloudWatchMetrics"][0]
    assert definition["Dimensions"] == [["Environment", "Queue"]]
    assert definition["Namespace"] == "DjangoTasksAws" and record["Failed"] == [1.0]
    with pytest.raises(ValueError):
        telemetry.emit_metric("arbitrary-user-input")


def test_cloudwatch_link_is_encoded_and_invalid_metadata_is_refused():
    url = inspection.build_cloudwatch_url(
        "eu-west-1", "/aws/lambda/documents", "2026/10/18/[$LATEST]x"
    )
    assert url.startswith("https://eu-west-1.console.amazonaws.com/")
    assert "$252Faws$252Flambda$252Fdocuments" in url
    for region, group, stream in (
        ("evil.example", "group", "stream"),
        ("eu-west-1", "", "x"),
        ("eu-west-1", "\n", "x"),
    ):
        assert inspection.build_cloudwatch_url(region, group, stream) is None


def test_logs_insights_link_is_encoded_and_invalid_metadata_is_refused():
    url = inspection.build_logs_insights_url(
        "eu-west-1", "/aws/lambda/documents", "a60fe477-313b-47e8-8bb3-85e928991b34"
    )
    assert url.startswith(
        "https://eu-west-1.console.amazonaws.com/cloudwatch/home?region=eu-west-1"
        "#logsV2:logs-insights$3FqueryDetail$3D~"
    )
    assert "*2faws*2flambda*2fdocuments" in url
    assert "a60fe477-313b-47e8-8bb3-85e928991b34" in url
    assert "*40timestamp" in url
    assert "filter*20task_id*20*3d*20*22" in url
    for region, group, task_id in (
        ("evil.example", "group", "id"),
        ("eu-west-1", "", "id"),
        ("eu-west-1", "group", ""),
        ("eu-west-1", "\n", "id"),
    ):
        assert inspection.build_logs_insights_url(region, group, task_id) is None


def test_all_correlation_fields_still_respect_event_byte_limit(caplog):
    with caplog.at_level(logging.INFO):
        telemetry.log_event(
            "large-event", **{field: "雪" * 1000 for field in telemetry.ALLOWED_FIELDS}
        )
    record = event_records(caplog)[-1]
    assert len(json.dumps(record, separators=(",", ":")).encode("utf-8")) <= 4096


def test_persisted_error_summary_redacts_credentials_and_is_bounded():
    record = telemetry.sanitize_error(
        {
            "error_type": "builtins.RuntimeError",
            "message": "password=hunter2 secret=private api_key=abcdefgh Bearer abc.jwt.sig "
            "https://user:pass@example.test " + "x" * 3000,
            "uncertain": True,
        }
    )
    assert len(record["message"]) <= 2000
    assert all(
        secret not in record["message"]
        for secret in ("hunter2", "private", "abcdefgh", "abc.jwt.sig", "user:pass")
    )
    assert record["uncertain"]


def test_workflow_metrics_are_known_and_dimension_bounded(capsys):
    telemetry.emit_metric("WorkflowRecoveries")
    record = metric_records(capsys)[0]
    assert record["WorkflowRecoveries"] == [1.0]
    assert record["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Environment"]]


def test_workflow_event_correlates_workflow_and_step(caplog):
    with caplog.at_level(logging.INFO):
        telemetry.log_event("workflow.recovery", workflow_id="wf-123", step_key="1")
    record = event_records(caplog)[0]
    assert record["workflow_id"] == "wf-123"
    assert record["step_key"] == "1"
