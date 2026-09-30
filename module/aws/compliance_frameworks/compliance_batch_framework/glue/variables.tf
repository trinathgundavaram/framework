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
  type    = map(string)
  default = {}
}

variable "artifacts_bucket" {
  description = "Existing bucket the Glue scripts and wheel are uploaded to."
  type        = string
}

variable "artifacts_bucket_key" {
  type    = string
  default = "compliance_batch_framework"
}

variable "glue_version" {
  type    = string
  default = "3.0"
}

variable "python_version" {
  type    = string
  default = "3.9"
}

variable "max_capacity" {
  description = "Default DPU of the runner; each workflow step sets its own."
  type        = number
  default     = 0.0625
}

variable "glue_security_conf" {
  description = "Glue security configuration name, if any."
  type        = string
  default     = null
}

variable "glue_connection_names" {
  description = "Existing Glue connections that give the jobs access to RDS."
  type        = list(string)
  default     = []
}

variable "pypi_packages" {
  description = "PyPI packages installed with the framework wheel."
  type        = list(string)
}

variable "runner_max_concurrent_runs" {
  type    = number
  default = 20
}

variable "rds_secret_name" {
  description = "Existing secret with host, port, username, password and dbname of the framework database."
  type        = string
}

variable "rds_database_name" {
  description = "Database name, when it differs from the secret's dbname."
  type        = string
  default     = null
}

variable "metadata_schema" {
  type    = string
  default = "cms_compliance"
}

variable "business_tz" {
  type    = string
  default = "America/Chicago"
}

variable "quarantine_uri" {
  description = "Where rejected files go."
  type        = string
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

variable "framework_settings" {
  description = "Other framework settings, by name without FRAMEWORK_."
  type        = map(string)
  default     = {}
}

variable "permissions_boundary" {
  description = "Permissions boundary ARN for the Glue role, if the account requires one."
  type        = string
  default     = null
}

variable "data_bucket_names" {
  description = "Buckets of the file configs (inbound, archive) and the quarantine."
  type        = list(string)
}

variable "kms_key_arns" {
  description = "KMS keys of the data buckets or the database secret, if any."
  type        = list(string)
  default     = []
}
