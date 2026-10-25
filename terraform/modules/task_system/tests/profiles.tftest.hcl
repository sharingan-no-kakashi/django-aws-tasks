mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/test" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:eu-west-1:123456789012:test", url = "https://sqs.eu-west-1.amazonaws.com/123456789012/test" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:eu-west-1:123456789012:function:test" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:eu-west-1:123456789012:log-group:test" } }
  mock_data "aws_region" { defaults = { region = "eu-west-1" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
}
variables {
  name                       = "test-tasks"
  image_uri                  = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/tasks@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  vpc_id                     = "vpc-test"
  subnet_ids                 = ["subnet-one", "subnet-two"]
  database_security_group_id = "sg-database"
  database_secret_arn        = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:database"
  environment                = { TASK_ENVIRONMENT = "test-tasks", DJANGO_SETTINGS_MODULE = "sample_project.settings" }
  alarm_actions              = ["arn:aws:sns:eu-west-1:123456789012:alarms"]
  workers = {
    short     = { timeout_seconds = 60, lease_seconds = 90, visibility_timeout_seconds = 360, concurrency = 10 }
    documents = { timeout_seconds = 300, lease_seconds = 360, visibility_timeout_seconds = 1800, concurrency = 4 }
  }
}
run "isolated_profiles" {
  command = apply
  assert {
    condition     = length(aws_sqs_queue.source) == 2 && length(aws_sqs_queue.dead) == 2 && length(aws_lambda_event_source_mapping.worker) == 2
    error_message = "Each route needs isolated transport and mapping resources."
  }
  assert {
    condition     = alltrue([for m in aws_lambda_event_source_mapping.worker : m.batch_size == 1 && m.maximum_batching_window_in_seconds == 0 && m.function_response_types == toset(["ReportBatchItemFailures"])])
    error_message = "Baseline mappings must report individual SQS failures."
  }
  assert {
    condition     = aws_lambda_function.function["documents"].timeout == 300 && aws_lambda_function.function["short"].reserved_concurrent_executions == 10 && aws_lambda_function.function["sweeper"].reserved_concurrent_executions == 1
    error_message = "Worker and sweeper limits must remain independent."
  }
  assert {
    condition     = aws_lambda_function.function["sweeper"].image_config[0].command == tolist(["django_tasks_aws.handlers.sweeper.lambda_handler"]) && aws_scheduler_schedule.sweeper.target[0].retry_policy[0].maximum_retry_attempts == 2
    error_message = "Recovery must use the packaged handler and bounded schedule retries."
  }
  assert {
    condition     = alltrue([for q in aws_sqs_queue.source : q.message_retention_seconds == 345600 && !q.fifo_queue]) && alltrue([for q in aws_sqs_queue.dead : q.message_retention_seconds == 1209600])
    error_message = "Transport retention must not silently change or enable FIFO."
  }
  assert {
    condition     = length(jsondecode(aws_iam_policy.producer.policy).Statement) == 1 && jsondecode(aws_iam_policy.producer.policy).Statement[0].Action[0] == "sqs:SendMessage"
    error_message = "Producers must not receive or delete worker messages."
  }
}
run "reject_mutable_image" {
  command = plan
  variables { image_uri = "example:latest" }
  expect_failures = [var.image_uri]
}
run "reject_unsafe_profile" {
  command = plan
  variables {
    workers = { short = { timeout_seconds = 60, lease_seconds = 60, visibility_timeout_seconds = 60, concurrency = 1, batch_size = 10 } }
  }
  expect_failures = [var.workers]
}
run "reject_missing_network" {
  command = plan
  variables { subnet_ids = [] }
  expect_failures = [var.subnet_ids]
}
run "reject_reserved_profile_identity" {
  command = plan
  variables {
    workers = { sweeper = { timeout_seconds = 60, lease_seconds = 90, visibility_timeout_seconds = 360, concurrency = 2 } }
  }
  expect_failures = [var.workers]
}

override_resource {
  target = aws_sqs_queue.source["short"]
  values = {
    arn = "arn:aws:sqs:eu-west-1:123456789012:test-tasks-short"
    url = "https://sqs.eu-west-1.amazonaws.com/123456789012/test-tasks-short"
  }
}
override_resource {
  target = aws_sqs_queue.source["documents"]
  values = {
    arn = "arn:aws:sqs:eu-west-1:123456789012:test-tasks-documents"
    url = "https://sqs.eu-west-1.amazonaws.com/123456789012/test-tasks-documents"
  }
}

run "reject_missing_application_configuration" {
  command = plan
  variables { environment = {} }
  expect_failures = [var.environment]
}
run "existing_mysql_listener" {
  command = plan
  variables { database_port = 3306 }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.database.from_port == 3306 && aws_vpc_security_group_ingress_rule.database.to_port == 3306 && aws_vpc_security_group_ingress_rule.database.security_group_id == "sg-database"
    error_message = "Supplied database ingress must use the configured listener and security group."
  }
}
run "reject_invalid_database_port" {
  command = plan
  variables { database_port = 65536 }
  expect_failures = [var.database_port]
}
