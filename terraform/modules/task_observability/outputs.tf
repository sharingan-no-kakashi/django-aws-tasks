output "dashboard_arn" {
  value = aws_cloudwatch_dashboard.tasks.dashboard_arn
}
output "alarm_arns" {
  value = concat(
    [for alarm in aws_cloudwatch_metric_alarm.recovery : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.function_errors : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.throttles : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.business_failures : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.publication_failures : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.queue_age : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.worker_dlq : alarm.arn],
    aws_cloudwatch_metric_alarm.scheduler_dlq[*].arn,
    aws_cloudwatch_metric_alarm.database_connections[*].arn,
  )
}
