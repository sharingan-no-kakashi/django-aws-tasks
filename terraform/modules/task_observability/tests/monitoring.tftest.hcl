mock_provider "aws" {}
variables {
  name                  = "test-tasks"
  environment           = "test"
  region                = "eu-west-1"
  sweeper_function_name = "test-sweeper"
  alarm_actions         = ["arn:aws:sns:eu-west-1:123456789012:operators"]
  workers = {
    short     = { function_name = "test-short", source_queue_name = "short", dlq_name = "short-dlq" }
    documents = { function_name = "test-documents", source_queue_name = "documents", dlq_name = "documents-dlq" }
  }
}
run "two_routes_and_independent_recovery_health" {
  command = plan
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.worker_dlq) == 2 && length(aws_cloudwatch_metric_alarm.function_errors) == 3
    error_message = "Each worker needs its own DLQ/errors and sweeper errors must be monitored."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.recovery["heartbeat"].treat_missing_data == "breaching"
    error_message = "Stopped sweepers must alarm even if no telemetry arrives."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.business_failures["short"].namespace == "DjangoTasksAws"
    error_message = "Handled business failures must use application metrics, not only Lambda Errors."
  }
  assert {
    condition     = contains(keys(aws_cloudwatch_metric_alarm.recovery), "hold_age") && contains(keys(aws_cloudwatch_metric_alarm.recovery), "due_age")
    error_message = "Held age and runnable delay need separate alarms."
  }
}
run "reject_unowned_alarms" {
  command = plan
  variables { alarm_actions = [] }
  expect_failures = [var.alarm_actions]
}
run "optional_workflow_health" {
  command = plan
  variables { workflows_enabled = true }
  assert {
    condition = length(aws_cloudwatch_metric_alarm.workflow) == 2 && length(aws_cloudwatch_metric_alarm.workflow_coordinator_missing) == 1
    error_message = "Workflow stalls, coordinator failures, and missing coordinator signals need separate alarms."
  }
}
