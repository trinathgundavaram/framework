variable "environment" {
  description = "dev, test or prod."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix of every resource name."
  type        = string
  default     = "compliance_batch_framework"
}

variable "required_common_tags" {
  description = "Tags on every resource."
  type        = map(string)
  default     = {}
}

variable "artifacts_bucket" {
  description = "Artifacts bucket (created here); globally unique."
  type        = string
}

variable "artifacts_bucket_key" {
  description = "Key prefix in the artifacts bucket."
  type        = string
  default     = "compliance_batch_framework"
}

variable "rds_secret_name" {
  description = "Secrets Manager secret (created here) holding the RDS credentials."
  type        = string
}

variable "rds_database_name" {
  type = string
}

variable "db_host" {
  description = "RDS endpoint."
  type        = string
}

variable "db_port" {
  type    = number
  default = 5432
}

variable "db_username" {
  description = "Set with TF_VAR_db_username."
  type        = string
  sensitive   = true
}

variable "db_password" {
  description = "Set with TF_VAR_db_password."
  type        = string
  sensitive   = true
}

variable "metadata_schema" {
  description = "Schema of the framework tables."
  type        = string
  default     = "cms_compliance"
}

variable "enable_vpc_connection" {
  description = "Run the jobs in the RDS VPC."
  type        = bool
  default     = true
}

variable "subnet_id" {
  description = "Private subnet for the Glue jobs."
  type        = string
  default     = null
}

variable "security_group_ids" {
  description = "Security groups for the Glue jobs."
  type        = list(string)
  default     = []
}

variable "availability_zone" {
  description = "Availability zone of subnet_id."
  type        = string
  default     = null
}

variable "data_bucket_names" {
  description = "Buckets of the file configs (inbound, archive) and the quarantine."
  type        = list(string)
}

variable "quarantine_uri" {
  description = "Where rejected files go."
  type        = string
}

variable "kms_key_arns" {
  description = "KMS keys of the data buckets, if any."
  type        = list(string)
  default     = []
}

variable "business_tz" {
  type    = string
  default = "America/Chicago"
}

variable "notify_from_email" {
  description = "SES-verified sender."
  type        = string
}

variable "default_notify_emails" {
  description = "Recipients of events not tied to one file config."
  type        = list(string)
  default     = []
}

variable "rule_engine" {
  description = "gre | none | module:Class."
  type        = string
  default     = "gre"
}

variable "gre_entrypoint" {
  description = "GRE_ENTRYPOINT, package.module:function."
  type        = string
  default     = ""
}

variable "extra_python_modules" {
  description = "Extra --additional-python-modules entries."
  type        = list(string)
  default     = []
}

variable "framework_settings" {
  description = "Other framework settings, by name without FRAMEWORK_."
  type        = map(string)
  default     = {}
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

variable "schedule_timezone" {
  description = "Time zone of the schedule expressions."
  type        = string
  default     = "America/Chicago"
}

variable "enable_schedules" {
  description = "false disables every schedule."
  type        = bool
  default     = true
}

variable "runner_max_concurrent_runs" {
  description = "Concurrent runs of the runner job."
  type        = number
  default     = 20
}

variable "workflow_timeout_hours" {
  description = "Longest a workflow execution may run."
  type        = number
  default     = 8
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
