output "route_manifest" { value = local.routes }
output "route_manifest_json" { value = jsonencode(local.routes) }
output "worker_functions" { value = { for name, w in var.workers : name => aws_lambda_function.function[name].function_name } }
output "sweeper_function" { value = aws_lambda_function.function["sweeper"].function_name }
output "producer_policy_arn" { value = aws_iam_policy.producer.arn }
output "function_security_group_id" { value = aws_security_group.lambda.id }
output "dashboard_arn" { value = module.observability.dashboard_arn }
output "alarm_arns" { value = module.observability.alarm_arns }

output "event_source_mapping_arns" { value = [for mapping in aws_lambda_event_source_mapping.worker : mapping.arn] }

output "schedule_name" { value = aws_scheduler_schedule.sweeper.name }

output "worker_dlq_urls" { value = { for name, q in aws_sqs_queue.dead : name => q.url } }
output "worker_dlq_arns" { value = { for name, q in aws_sqs_queue.dead : name => q.arn } }
