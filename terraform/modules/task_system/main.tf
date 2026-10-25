data "aws_region" "current" {}
data "aws_partition" "current" {}
locals {
  functions = merge({ for name, w in var.workers : name => {
    timeout = w.timeout_seconds, memory = w.memory_size, concurrency = w.concurrency,
    command = "django_tasks_aws.handlers.worker.lambda_handler"
    } }, { sweeper = {
    timeout = 60, memory = 512, concurrency = 1,
    command = "django_tasks_aws.handlers.sweeper.lambda_handler"
  } })
  routes = { for name, w in var.workers : name => {
    source_queue_arn           = aws_sqs_queue.source[name].arn
    source_queue_url           = aws_sqs_queue.source[name].url
    timeout_seconds            = w.timeout_seconds
    lease_seconds              = w.lease_seconds
    visibility_timeout_seconds = w.visibility_timeout_seconds
    concurrency                = w.concurrency
    max_receive_count          = w.max_receive_count
    dispatch_limit             = w.dispatch_limit
    redispatch_seconds         = w.redispatch_seconds
  } }
  lambda_trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_sqs_queue" "dead" {
  for_each                  = var.workers
  name                      = "${var.name}-${each.key}-dead"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}
resource "aws_sqs_queue" "source" {
  fifo_queue                 = false
  for_each                   = var.workers
  name                       = "${var.name}-${each.key}"
  visibility_timeout_seconds = each.value.visibility_timeout_seconds
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dead[each.key].arn, maxReceiveCount = each.value.max_receive_count })
  tags                       = var.tags
}
resource "aws_sqs_queue_redrive_allow_policy" "dead" {
  for_each             = var.workers
  queue_url            = aws_sqs_queue.dead[each.key].url
  redrive_allow_policy = jsonencode({ redrivePermission = "byQueue", sourceQueueArns = [aws_sqs_queue.source[each.key].arn] })
}
resource "aws_security_group" "lambda" {
  name_prefix = "${var.name}-lambda-"
  vpc_id      = var.vpc_id
  description = "Task functions; service egress requires consumer routes or endpoints"
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = var.tags
}
resource "aws_vpc_security_group_ingress_rule" "database" {
  security_group_id            = var.database_security_group_id
  referenced_security_group_id = aws_security_group.lambda.id
  ip_protocol                  = "tcp"
  from_port                    = var.database_port
  to_port                      = var.database_port
}
resource "aws_iam_role" "function" {
  for_each           = local.functions
  name               = "${var.name}-${each.key}"
  assume_role_policy = local.lambda_trust
  tags               = var.tags
}
resource "aws_cloudwatch_log_group" "function" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = 30
  tags              = var.tags
}
resource "aws_iam_role_policy" "function" {
  for_each = local.functions
  role     = aws_iam_role.function[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.function[each.key].arn}:*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.database_secret_arn },
    { Effect = "Allow", Action = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"], Resource = "*" }
    ], each.key == "sweeper" ? [
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = [for q in aws_sqs_queue.source : q.arn] }
    ] : [
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = [aws_sqs_queue.source[each.key].arn] }
    ], length(var.kms_key_arns) == 0 ? [] : [
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.kms_key_arns }
  ]) })
}
resource "aws_iam_role_policy" "business" {
  for_each = var.business_policy_json == null ? {} : var.workers
  role     = aws_iam_role.function[each.key].id
  policy   = var.business_policy_json
}
resource "aws_lambda_function" "function" {
  for_each                       = local.functions
  function_name                  = "${var.name}-${each.key}"
  package_type                   = "Image"
  image_uri                      = var.image_uri
  role                           = aws_iam_role.function[each.key].arn
  timeout                        = each.value.timeout
  memory_size                    = each.value.memory
  reserved_concurrent_executions = each.value.concurrency
  architectures                  = ["x86_64"]
  image_config { command = [each.value.command] }
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }
  environment {
    variables = merge(var.environment, {
      TASK_WORKER_ROUTES       = jsonencode(local.routes)
      TASK_DATABASE_SECRET_ARN = var.database_secret_arn
      }, each.key == "sweeper" ? {} : {
      TASK_WORKER_NAME       = each.key
      TASK_WORKER_SOURCE_ARN = aws_sqs_queue.source[each.key].arn
    })
  }
  depends_on = [aws_iam_role_policy.function, aws_cloudwatch_log_group.function, aws_vpc_security_group_ingress_rule.database]
  tags       = var.tags
}
resource "aws_lambda_event_source_mapping" "worker" {
  for_each                           = var.workers
  event_source_arn                   = aws_sqs_queue.source[each.key].arn
  function_name                      = aws_lambda_function.function[each.key].arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  function_response_types            = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = each.value.concurrency }
}
resource "aws_iam_policy" "producer" {
  name = "${var.name}-producer"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = [for q in aws_sqs_queue.source : q.arn] }
  ] })
  tags = var.tags
}
resource "aws_sqs_queue" "scheduler_dead" {
  name                      = "${var.name}-scheduler-dead"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}
resource "aws_iam_role" "scheduler" {
  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }, ArnLike = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:scheduler:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:schedule-group/default" } }
  }] })
  tags = var.tags
}
data "aws_caller_identity" "current" {}
resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.function["sweeper"].arn },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.scheduler_dead.arn }
  ] })
}
resource "aws_scheduler_schedule" "sweeper" {
  name                = "${var.name}-reconcile"
  schedule_expression = "rate(1 minute)"
  state               = var.schedule_state
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.function["sweeper"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
    dead_letter_config { arn = aws_sqs_queue.scheduler_dead.arn }
  }
  depends_on = [aws_iam_role_policy.scheduler]
}
module "observability" {
  database_instance_id = var.database_instance_id
  source               = "../task_observability"
  name                 = var.name
  environment          = lookup(var.environment, "TASK_ENVIRONMENT", var.name)
  region               = data.aws_region.current.region
  workers = { for name, w in var.workers : name => {
    function_name     = aws_lambda_function.function[name].function_name
    source_queue_name = aws_sqs_queue.source[name].name
    dlq_name          = aws_sqs_queue.dead[name].name
  } }
  sweeper_function_name = aws_lambda_function.function["sweeper"].function_name
  scheduler_dlq_name    = aws_sqs_queue.scheduler_dead.name
  alarm_actions         = var.alarm_actions
  tags                  = var.tags
}
