variable "env" {
  type = string
}

variable "name_prefix" {
  description = "Start of every resource name; must start with compliance (the deployer role only manages compliance* resources)."
  type        = string
  default     = "compliance_batch_framework"

  validation {
    condition     = can(regex("^compliance[a-z0-9_]*$", var.name_prefix))
    error_message = "name_prefix must start with compliance and use lower-case letters, digits or _."
  }
}

variable "required_common_tags" {
  description = "Tags on every resource."
  type        = map(string)
  default     = { AppName = "Compliance", ManagedBy = "Terraform" }
}

variable "projects" {
  description = "One entry per project (key = Project_Cd, or all_projects for steps without --project): settings, alert_emails, schedules. See README."
  type = map(object({
    enabled      = optional(bool, true)
    settings     = optional(map(string), {})
    alert_emails = optional(list(string), [])
    schedules = optional(map(object({
      expression = string
      steps      = list(string)
      run_type   = optional(string)
      period     = optional(string)
      table      = optional(string)
      as_of      = optional(string)
      enabled    = optional(bool, true)
    })), {})
  }))
  default = {}

  validation {
    condition     = alltrue([for k in keys(var.projects) : can(regex("^[A-Za-z0-9_-]{1,30}$", k))])
    error_message = "Project keys are the Project_Cd (or all_projects): letters, digits, _ or -, at most 30 characters."
  }
}

variable "step_settings" {
  description = "Per step: Glue DPU, timeout minutes, and whether exit 1 stops the workflow."
  type = map(object({
    capacity         = number
    timeout_minutes  = number
    fail_on_problems = bool
  }))
  default = {
    BATCH_CREATION     = { capacity = 0.0625, timeout_minutes = 60, fail_on_problems = true }
    FILE_LOAD          = { capacity = 1, timeout_minutes = 120, fail_on_problems = false }
    OVERRIDE_DECISIONS = { capacity = 0.0625, timeout_minutes = 30, fail_on_problems = false }
    BATCH_CLOSE        = { capacity = 0.0625, timeout_minutes = 30, fail_on_problems = true }
    NOTIFY             = { capacity = 0.0625, timeout_minutes = 30, fail_on_problems = true }
  }

  validation {
    condition = alltrue([for k, v in var.step_settings :
    contains(["BATCH_CREATION", "FILE_LOAD", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY"], k) && contains([0.0625, 1], v.capacity) && v.timeout_minutes > 0])
    error_message = "step_settings keys are framework modules; capacity is 0.0625 or 1 (Glue Python Shell); timeout_minutes > 0."
  }
}

variable "enable_workflow_logs" {
  description = "Send workflow errors to CloudWatch Logs; the deployer role needs the logs:*LogDelivery actions on *."
  type        = bool
  default     = false
}

variable "enable_schedules" {
  description = "false disables every schedule."
  type        = bool
  default     = true
}

variable "workflow_timeout_hours" {
  type    = number
  default = 8
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "enable_failure_alerts" {
  type    = bool
  default = true
}

variable "alert_email" {
  description = "Operations email for workflow failures and the schedule dead-letter alarm."
  type        = string
  default     = ""
}

variable "permissions_boundary" {
  description = "Permissions boundary ARN for the roles, if the account requires one."
  type        = string
  default     = null
}
