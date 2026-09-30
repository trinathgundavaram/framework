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

variable "permissions_boundary" {
  description = "Permissions boundary ARN for the role, if the account requires one."
  type        = string
  default     = null
}

variable "artifacts_bucket" {
  description = "Existing bucket the Glue scripts and wheel are uploaded to."
  type        = string
}

variable "artifacts_bucket_key" {
  type    = string
  default = "compliance_batch_framework"
}

variable "data_bucket_names" {
  description = "Buckets of the file configs (inbound, archive) and the quarantine."
  type        = list(string)
}

variable "kms_key_arns" {
  description = "KMS keys of the data buckets, if any."
  type        = list(string)
  default     = []
}

variable "rds_secret_name" {
  type = string
}

variable "notify_from_email" {
  description = "SES-verified sender."
  type        = string
}
