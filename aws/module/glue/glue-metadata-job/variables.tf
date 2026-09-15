variable "job_name" {
  description = "AWS Glue job name."
  type        = string
}

variable "environment" {
  type = string
}

variable "script_s3_uri" {
  description = "s3:// URI of glue_job_metadata_load.py (from the s3-artifacts module)."
  type        = string
}

variable "s3_input_path" {
  description = "s3:// prefix the job reads <table_key>.csv from (--S3_INPUT_PATH)."
  type        = string
}

variable "ddl_s3_path" {
  description = "s3:// URI of create_metadata_tables.sql (--DDL_S3_PATH)."
  type        = string
}

variable "s3_bucket_arn" {
  description = "ARN of the artifacts bucket, for scoping the IAM read policy."
  type        = string
}

variable "secret_arn" {
  description = "Secrets Manager secret ARN holding the Postgres credentials (--SECRET_NAME)."
  type        = string
}

variable "run_ddl" {
  description = "Whether the job applies ddl/create_metadata_tables.sql on each run (--RUN_DDL). Safe to leave true - it's CREATE TABLE IF NOT EXISTS."
  type        = bool
  default     = true
}

variable "tables" {
  description = "Optional comma-separated subset of table keys to load (--TABLES). Empty = all 5 tables."
  type        = string
  default     = ""
}

variable "additional_python_modules" {
  description = "Python packages installed for the Python Shell job."
  type        = string
  default     = "psycopg2-binary,pandas"
}

variable "glue_version" {
  description = "Glue version for the Python Shell job."
  type        = string
  default     = "3.0"
}

variable "python_version" {
  description = "Python version for the Python Shell job (3.9 is the current Glue Python Shell runtime)."
  type        = string
  default     = "3.9"
}

variable "max_capacity" {
  description = "DPUs for a Python Shell job: 0.0625 or 1."
  type        = number
  default     = 0.0625
}

variable "timeout_minutes" {
  type    = number
  default = 30
}

variable "max_retries" {
  type    = number
  default = 0
}

variable "temp_dir_s3_uri" {
  description = "s3:// prefix Glue uses as scratch space (--TempDir)."
  type        = string
}

# --- Optional VPC connectivity, if Postgres is only reachable from inside a VPC ---
variable "enable_vpc_connection" {
  description = "Set true if Postgres is private (e.g. RDS in a VPC) and the job needs a Glue network connection to reach it."
  type        = bool
  default     = false
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

# --- Optional schedule (defaults to on-demand only) ---
variable "enable_schedule" {
  description = "Set true to add a Glue trigger that runs the job on a cron schedule (for recurring ad-hoc-update runs). Leave false to only run on demand."
  type        = bool
  default     = false
}

variable "schedule_cron" {
  description = "Glue cron expression, e.g. \"cron(0 6 * * ? *)\" for 6am UTC daily. Required if enable_schedule = true."
  type        = string
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
