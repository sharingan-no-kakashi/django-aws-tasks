# Task observability

Phase F monitoring for an existing task deployment. This module creates only
CloudWatch alarms and a dashboard; it does not create workers, queues, RDS,
Scheduler, SNS subscriptions, or networking. It can be composed with the Phase E
stack once that stack exists.

```hcl
module "task_observability" {
  source = "../../modules/task_observability"
  name = "sample-tasks"
  environment = "sample"
  region = "eu-west-1"
  sweeper_function_name = "sample-sweeper"
  scheduler_dlq_name = "sample-scheduler-delivery-failures"
  alarm_actions = ["arn:aws:sns:eu-west-1:123456789012:task-operators"]
  workers = {
    short = {
      function_name = "sample-short"
      source_queue_name = "sample-short-tasks"
      dlq_name = "sample-short-dlq"
    }
    documents = {
      function_name = "sample-documents"
      source_queue_name = "sample-document-tasks"
      dlq_name = "sample-documents-dlq"
    }
  }
  database_instance_id = "sample-writer"
  database_connection_threshold = 100
}
```

Use actual deployment output names and configure the AWS provider for the same
region. No secret values are inputs or outputs. `environment` must match
`DJANGO_TASKS_AWS_ENVIRONMENT`, and the application must emit raw JSON EMF logs
at INFO. Lambda logs must reach CloudWatch. Required SNS destinations keep
alarm ownership explicit; verify subscriptions and notification delivery in the
target account. `alarm_arns` and `dashboard_arn` expose created resource references.

The default overdue threshold is 300 seconds and held-age threshold 3600 seconds.
Missing heartbeat data breaches after three 60-second periods. Ordinary sparse
business metrics treat missing data as non-breaching. New holds and terminal
application failures alert independently of Lambda Errors. RDS and Scheduler
DLQ alarms are included when their identifiers are supplied. Alarm actions are
sent for both ALARM and OK transitions.

Run without deploying resources:

```sh
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

Tests use a mocked AWS provider. They establish resource bindings and alarm
configuration, not live delivery, invocation behavior, log extraction, or cost.
Deployment and fault-drill evidence are separate. Thresholds and alarm counts
should be reviewed against the application's workload and CloudWatch charges.
See [operations](../../../docs/operations.md) for the incident and pause runbook.
