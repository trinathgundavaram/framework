##############################################################################
# CMS Compliance Framework - metadata Glue job
#
# Specific Terraform for this one deployment (dev/test/prod via co-located
# .tfvars files, applied through Terragrunt) - not a generic reusable module.
# Variables here are only the things that actually differ across
# environments or accounts; fixed design decisions (Python Shell 3.9,
# 0.0625 DPU, pg8000/pandas, 30-minute timeout, execution_property) are
# hardcoded directly in glue-job.tf.
##############################################################################

variable "environment" {
  description = "dev / test / prod"
  type        = string
}

variable "region" {
  description = "AWS region everything is deployed into."
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "AWS account id, used to build the Glue role ARN convention."
  type        = string
}

# --- s3-artifacts -----------------------------------------------------------

variable "artifacts_bucket" {
  description = "Name of the S3 bucket that holds the Glue script, DDL, and seed CSVs. Must be globally unique."
  type        = string
}

variable "artifacts_bucket_key" {
  description = "Key prefix inside artifacts_bucket this job's files are uploaded under, e.g. \"cms-compliance-metadata\"."
  type        = string
  default     = "cms-compliance-metadata"
}

# --- secrets -----------------------------------------------------------------

variable "rds_secret_name" {
  description = "Secrets Manager secret name/id holding the Postgres credentials for this environment."
  type        = string
}

variable "rds_database_name" {
  description = "Postgres database name the job connects to."
  type        = string
}

variable "db_host" {
  description = "Postgres host/endpoint."
  type        = string
}

variable "db_port" {
  type    = number
  default = 5432
}

variable "db_username" {
  type      = string
  sensitive = true
}

variable "db_password" {
  description = "Pass via TF_VAR_db_password (or an env-specific secrets pipeline) at apply time - never commit this in a .tfvars file."
  type        = string
  sensitive   = true
}

# --- glue-job ------------------------------------------------------------------

variable "job_name" {
  description = "Glue job name, e.g. maa_cms_compliance_metadata_load_dev."
  type        = string
}

variable "glue_role_name" {
  description = "Name of the existing IAM role Glue assumes, following the same ENTERPRISE/<ROLE> convention as the team's other Glue jobs."
  type        = string
}

# --- Only set when Postgres is private/VPC-only -----------------------------

variable "enable_vpc_connection" {
  type    = bool
  default = false
}

variable "subnet_id" {
  type    = string
  default = null
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

variable "availability_zone" {
  type    = string
  default = null
}

# --- Recurring ad-hoc-update runs; on-demand only if left false -------------

variable "enable_schedule" {
  type    = bool
  default = false
}

variable "schedule_cron" {
  description = "Glue cron expression, e.g. \"cron(0 6 * * ? *)\". Required if enable_schedule = true."
  type        = string
  default     = ""
}

# --- Failure alerting (SNS + CloudWatch, same pattern as the team's other Glue jobs) ---

variable "enable_failure_alerts" {
  type    = bool
  default = true
}

variable "alert_email" {
  description = "Email address subscribed to the job-failure SNS topic."
  type        = string
  default     = ""
}

variable "required_common_tags" {
  description = "Standard tag block applied to every resource, same convention as the team's other Terraform."
  type        = map(string)
  default     = {}
}
