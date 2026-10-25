variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,29}$", var.name))
    error_message = "Use a lowercase resource prefix of at most 30 characters."
  }
}
variable "image_uri" {
  type = string
  validation {
    condition     = can(regex("^[^ ]+@sha256:[a-f0-9]{64}$", var.image_uri))
    error_message = "An immutable ECR image digest is required; tags are not release identities."
  }
}
variable "workers" {
  type = map(object({
    timeout_seconds            = number
    lease_seconds              = number
    visibility_timeout_seconds = number
    concurrency                = number
    memory_size                = optional(number, 512)
    max_receive_count          = optional(number, 5)
    dispatch_limit             = optional(number, 12)
    redispatch_seconds         = optional(number, 300)
    batch_size                 = optional(number, 1)
    batch_window               = optional(number, 0)
  }))
  validation {
    condition = length(var.workers) > 0 && alltrue([for name, w in var.workers :
      can(regex("^[a-z][a-z0-9-]{0,19}$", name)) && name != "sweeper" &&
      w.timeout_seconds >= 6 && w.timeout_seconds <= 900 &&
      w.lease_seconds >= w.timeout_seconds + 5 &&
      w.visibility_timeout_seconds >= 6 * w.timeout_seconds &&
      w.visibility_timeout_seconds <= 43200 &&
      w.concurrency >= 2 && w.concurrency <= 1000 && floor(w.concurrency) == w.concurrency &&
      w.memory_size >= 128 && w.memory_size <= 10240 && w.memory_size % 64 == 0 &&
      w.max_receive_count >= 5 && w.max_receive_count <= 1000 && floor(w.max_receive_count) == w.max_receive_count &&
      w.dispatch_limit >= 1 && floor(w.dispatch_limit) == w.dispatch_limit &&
      w.redispatch_seconds >= 1 && floor(w.redispatch_seconds) == w.redispatch_seconds &&
      floor(w.timeout_seconds) == w.timeout_seconds && floor(w.lease_seconds) == w.lease_seconds &&
      floor(w.visibility_timeout_seconds) == w.visibility_timeout_seconds &&
      w.batch_size == 1 && w.batch_window == 0
    ])
    error_message = "Profiles require valid names, bounded resources, lease > timeout, visibility >= 6x timeout, positive budgets, and batch one/window zero."
  }
}
variable "subnet_ids" {
  type = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 2 && length(distinct(var.subnet_ids)) == length(var.subnet_ids)
    error_message = "Provide private subnets in at least two availability zones."
  }
}
variable "vpc_id" { type = string }
variable "database_security_group_id" { type = string }
variable "database_secret_arn" { type = string }
variable "database_port" {
  type        = number
  default     = 5432
  description = "Writer listener port of the supplied database; does not provision a database."
  validation {
    condition     = var.database_port >= 1 && var.database_port <= 65535 && floor(var.database_port) == var.database_port
    error_message = "database_port must be an integer between 1 and 65535."
  }
}
variable "kms_key_arns" {
  type    = list(string)
  default = []
}
variable "environment" {
  type = map(string)
  validation {
    condition     = length(lookup(var.environment, "DJANGO_SETTINGS_MODULE", "")) > 0 && length(lookup(var.environment, "TASK_ENVIRONMENT", "")) > 0 && length(setintersection(toset(keys(var.environment)), toset(["AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "_HANDLER"]))) == 0
    error_message = "Set DJANGO_SETTINGS_MODULE and TASK_ENVIRONMENT; AWS reserved environment keys are prohibited."
  }
}
variable "alarm_actions" {
  type = list(string)
  validation {
    condition     = length(var.alarm_actions) > 0
    error_message = "An owned SNS alarm destination is required."
  }
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "business_policy_json" {
  type        = string
  default     = null
  description = "Optional consumer business permissions, separately attached to worker roles."
}
variable "schedule_state" {
  type    = string
  default = "ENABLED"
  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.schedule_state)
    error_message = "schedule_state must be ENABLED or DISABLED."
  }
}

variable "database_instance_id" {
  type    = string
  default = null
}
