variable "bucket_name" {
  description = "Name of the S3 bucket that holds the Glue script, DDL, and seed CSVs. Must be globally unique."
  type        = string
}

variable "environment" {
  description = "Environment name (dev / test / prod) - used for tagging."
  type        = string
}

variable "glue_script_path" {
  description = "Local path to glue_job_metadata_load.py to upload."
  type        = string
}

variable "ddl_script_path" {
  description = "Local path to create_metadata_tables.sql to upload."
  type        = string
}

variable "seed_data_dir" {
  description = "Local directory containing the one-time seed CSV files, one per table."
  type        = string
}

variable "force_destroy" {
  description = "Allow the bucket to be destroyed even if it still contains objects. Leave false in prod."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Optional KMS key ARN for SSE-KMS bucket encryption. Leave null to use SSE-S3 (AES256)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Additional tags to apply to all resources in this module."
  type        = map(string)
  default     = {}
}
