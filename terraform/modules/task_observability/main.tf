locals {
  recovery_alarms = {
    invariants = { metric = "InvariantViolations", comparison = "GreaterThanThreshold", threshold = 0, missing = "notBreaching", statistic = "Maximum", periods = 1 }
    heartbeat  = { metric = "Heartbeat", comparison = "LessThanThreshold", threshold = 1, missing = "breaching", statistic = "Sum", periods = 3 }
    due_age    = { metric = "OldestDueAge", comparison = "GreaterThanThreshold", threshold = var.oldest_due_seconds, missing = "notBreaching", statistic = "Maximum", periods = 3 }
    holds      = { metric = "HeldBacklog", comparison = "GreaterThanThreshold", threshold = 0, missing = "notBreaching", statistic = "Maximum", periods = 1 }
    hold_age   = { metric = "OldestHoldAge", comparison = "GreaterThanThreshold", threshold = var.oldest_hold_seconds, missing = "notBreaching", statistic = "Maximum", periods = 1 }
    expired    = { metric = "ExpiredLeases", comparison = "GreaterThanThreshold", threshold = 0, missing = "notBreaching", statistic = "Maximum", periods = 3 }
  }
  functions = merge({ for name, worker in var.workers : name => worker.function_name }, { sweeper = var.sweeper_function_name })
}
resource "aws_cloudwatch_metric_alarm" "recovery" {
  for_each            = local.recovery_alarms
  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = "Task recovery ${each.key}; use the operations runbook before releasing or replaying work."
  namespace           = "DjangoTasksAws"
  metric_name         = each.value.metric
  dimensions          = { Environment = var.environment }
  comparison_operator = each.value.comparison
  threshold           = each.value.threshold
  statistic           = each.value.statistic
  period              = 60
  evaluation_periods  = each.value.periods
  treat_missing_data  = each.value.missing
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "function_errors" {
  for_each            = local.functions
  alarm_name          = "${var.name}-${each.key}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "throttles" {
  for_each            = local.functions
  alarm_name          = "${var.name}-${each.key}-throttles"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "business_failures" {
  for_each            = var.workers
  alarm_name          = "${var.name}-${each.key}-business-failures"
  namespace           = "DjangoTasksAws"
  metric_name         = "Failed"
  dimensions          = { Environment = var.environment, Queue = each.key }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "publication_failures" {
  for_each            = var.workers
  alarm_name          = "${var.name}-${each.key}-publication-failures"
  namespace           = "DjangoTasksAws"
  metric_name         = "DispatchFailures"
  dimensions          = { Environment = var.environment, Queue = each.key }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "queue_age" {
  for_each            = var.workers
  alarm_name          = "${var.name}-${each.key}-queue-age"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = each.value.source_queue_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = var.oldest_due_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "worker_dlq" {
  for_each            = var.workers
  alarm_name          = "${var.name}-${each.key}-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value.dlq_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "scheduler_dlq" {
  count               = var.scheduler_dlq_name == null ? 0 : 1
  alarm_name          = "${var.name}-scheduler-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.scheduler_dlq_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "database_connections" {
  count               = var.database_instance_id == null ? 0 : 1
  alarm_name          = "${var.name}-database-connections"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBInstanceIdentifier = var.database_instance_id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = var.database_connection_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_dashboard" "tasks" {
  dashboard_name = var.name
  dashboard_body = jsonencode({ widgets = concat([
    { type = "metric", width = 12, height = 6, properties = {
      title   = "Recovery heartbeat and backlog", region = var.region, view = "timeSeries",
      metrics = [for metric in ["Heartbeat", "HeldBacklog", "ExpiredLeases"] : ["DjangoTasksAws", metric, "Environment", var.environment]]
    } },
    { type = "metric", width = 12, height = 6, properties = {
      title   = "Runnable age and held age (seconds)", region = var.region, view = "timeSeries",
      metrics = [for metric in ["OldestDueAge", "OldestHoldAge"] : ["DjangoTasksAws", metric, "Environment", var.environment]]
    } }
    ], var.workflows_enabled ? [{ type = "metric", width = 12, height = 6, properties = {
      title   = "Workflow progress and coordinator health", region = var.region, view = "timeSeries",
      metrics = [for metric in ["WorkflowCoordinatorHeartbeat", "WorkflowDeferred", "WorkflowStalled", "WorkflowAdvanceDelay", "WorkflowReconcileErrors"] : ["DjangoTasksAws", metric, "Environment", var.environment]]
      } }] : [], [for name, worker in var.workers : {
      type = "metric", width = 12, height = 6, properties = {
        title   = "${name} application outcomes", region = var.region, view = "timeSeries",
        metrics = [for metric in ["AttemptsStarted", "Successful", "Failed", "Retried", "Held", "DispatchFailures"] : ["DjangoTasksAws", metric, "Environment", var.environment, "Queue", name]]
      }
      }], [for name, worker in var.workers : {
      type = "metric", width = 12, height = 6, properties = {
        title = "${name} delivery age and dead letters", region = var.region, view = "timeSeries",
        metrics = [
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", worker.source_queue_name],
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", worker.dlq_name]
        ]
      }
      }], [for name, function in local.functions : {
      type = "metric", width = 12, height = 6, properties = {
        title   = "${name} Lambda health", region = var.region, view = "timeSeries",
        metrics = [for metric in ["Errors", "Throttles", "ConcurrentExecutions"] : ["AWS/Lambda", metric, "FunctionName", function]]
      }
      }], var.database_instance_id == null ? [] : [{
      type = "metric", width = 12, height = 6, properties = {
        title   = "Database connection pressure", region = var.region, view = "timeSeries",
        metrics = [["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.database_instance_id]]
      }
    }])
  })
}
resource "aws_cloudwatch_metric_alarm" "workflow" {
  for_each            = var.workflows_enabled ? toset(["WorkflowStalled", "WorkflowReconcileErrors"]) : toset([])
  alarm_name          = "${var.name}-${each.value}"
  alarm_description   = "Workflow advancement needs review; inspect workflow.waiting and workflow.reconcile_error logs."
  namespace           = "DjangoTasksAws"
  metric_name         = each.value
  dimensions          = { Environment = var.environment }
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
}
resource "aws_cloudwatch_metric_alarm" "workflow_coordinator_missing" {
  count               = var.workflows_enabled ? 1 : 0
  alarm_name          = "${var.name}-workflow-coordinator-missing"
  alarm_description   = "The task sweeper emits heartbeats but workflow coordination does not. Check optional app registration and sweep budgets."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
  tags                = var.tags
  metric_query {
    id          = "missing"
    expression  = "IF(FILL(core,0)>0,IF(FILL(coordinator,0)<1,1,0),0)"
    label       = "Workflow coordinator missing while task sweeper runs"
    return_data = true
  }
  metric_query {
    id = "core"
    metric {
      namespace   = "DjangoTasksAws"
      metric_name = "Heartbeat"
      dimensions  = { Environment = var.environment }
      period      = 60
      stat        = "Sum"
    }
  }
  metric_query {
    id = "coordinator"
    metric {
      namespace   = "DjangoTasksAws"
      metric_name = "WorkflowCoordinatorHeartbeat"
      dimensions  = { Environment = var.environment }
      period      = 60
      stat        = "Sum"
    }
  }
}
