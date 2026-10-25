variable "name" {
  type        = string
  description = "Unique monitoring resource prefix."
  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]{1,80}$", var.name))
    error_message = "name must be 1–80 letters, numbers, underscores, or hyphens."
  }
}
variable "environment" {
  type        = string
  description = "Must equal DJANGO_TASKS_AWS_ENVIRONMENT in the application."
  validation {
    condition     = length(var.environment) > 0 && length(var.environment) <= 64
    error_message = "environment must contain 1–64 characters."
  }
}
variable "region" {
  type        = string
  description = "Region containing the monitored resources."
}
variable "workers" {
  type = map(object({
    function_name     = string
    source_queue_name = string
    dlq_name          = string
  }))
  description = "Actual function/queue names from the task deployment's outputs."
  validation {
    condition     = length(var.workers) > 0
    error_message = "At least one deployed worker profile must be supplied."
  }
}
variable "sweeper_function_name" {
  type        = string
  description = "The deployed sweeper Lambda name."
}
variable "scheduler_dlq_name" {
  type        = string
  default     = null
  description = "Scheduler delivery-failure queue, distinct from worker DLQs."
}
variable "alarm_actions" {
  type        = list(string)
  description = "Owned SNS alarm destinations. The module does not create subscriptions."
  validation {
    condition     = length(var.alarm_actions) > 0 && alltrue([for arn in var.alarm_actions : can(regex("^arn:[^:]+:sns:", arn))])
    error_message = "Supply at least one owned SNS topic ARN for actionable notifications."
  }
}
variable "oldest_due_seconds" {
  type    = number
  default = 300
  validation {
    condition     = var.oldest_due_seconds > 0
    error_message = "oldest_due_seconds must be positive."
  }
}
variable "oldest_hold_seconds" {
  type    = number
  default = 3600
  validation {
    condition     = var.oldest_hold_seconds > 0
    error_message = "oldest_hold_seconds must be positive."
  }
}
variable "database_instance_id" {
  type        = string
  default     = null
  description = "Optional existing RDS instance identifier."
}
variable "database_connection_threshold" {
  type    = number
  default = 100
  validation {
    condition     = var.database_connection_threshold > 0
    error_message = "database_connection_threshold must be positive."
  }
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "workflows_enabled" {
  type        = bool
  default     = false
  description = "Enable workflow coordinator alarms only when the optional workflow app is installed."
}
