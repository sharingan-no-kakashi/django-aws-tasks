# Terraform module: task_system

Reusable Terraform module for the `django-tasks-aws` task system. Implemented
in DT-022 through DT-028.

The module provisions, per named worker profile, a source SQS queue, DLQ,
Lambda function, event source mapping, and log group; plus the sweeper function
and EventBridge Scheduler schedule.

`database_port` defaults to 5432 and controls the worker-to-writer ingress rule.
For supplied MySQL/MariaDB or Oracle databases, pass their listener port (commonly
3306 or 1521) and engine-specific consumer settings/driver image. This module
references the supplied database security group and secret; it does not provision
or replace the database. The sample bootstrap remains PostgreSQL-only. A plan on
a different port is not evidence that the selected engine/image has executed.
