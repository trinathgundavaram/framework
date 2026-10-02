variable "env" {
  type = string
}

variable "required_common_tags" {
  description = "Tags on every resource (env-config/<region>/common.tfvars)."
  type        = map(string)
  default     = {}
}

variable "glue_version" {
  description = "Ignored by AWS for Python shell jobs (the runtime is python_version); null avoids a perpetual diff."
  type        = string
  default     = null
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

variable "rds_secret_name" {
  description = "Existing secret with host, port, username, password and dbname of the framework database."
  type        = string
}

variable "rds_database_name" {
  description = "Metadata database name, passed to the jobs; null = the dbname of the secret."
  type        = string
  default     = null
}

variable "metadata_schema" {
  description = "Schema of the framework tables, passed to the jobs as --FRAMEWORK_METADATA_SCHEMA / --METADATA_SCHEMA."
  type        = string
  default     = "cms_compliance"

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]{0,62}$", var.metadata_schema))
    error_message = "metadata_schema must be a plain identifier."
  }
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

variable "alert_emails" {
  description = "Emailed when a Glue job run fails, times out or errors (including runs started by hand)."
  type        = list(string)
  default     = []
}
