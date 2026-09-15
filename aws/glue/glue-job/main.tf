##############################################################################
# CMS Compliance Framework - metadata-load AWS Glue Python Shell job
#
# Specific to this deployment (dev/test/prod via Terragrunt inputs) - not a
# generic reusable module. Runs glue_job_metadata_load.py (uploaded to S3 by
# the s3-artifacts component) to load/upsert the 5 metadata tables into
# Postgres, using credentials from the secrets component.
##############################################################################

variable "job_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "script_s3_uri" {
  description = "s3:// URI of glue_job_metadata_load.py (from the s3-artifacts component)."
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

variable "temp_dir_s3_uri" {
  description = "s3:// prefix Glue uses as scratch space (--TempDir)."
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

# --- Only set when Postgres is private/VPC-only ---
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

# --- Recurring ad-hoc-update runs; on-demand only if left false ---
variable "enable_schedule" {
  type    = bool
  default = false
}

variable "schedule_cron" {
  description = "Glue cron expression, e.g. \"cron(0 6 * * ? *)\". Required if enable_schedule = true."
  type        = string
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_iam_role" "glue" {
  name = "${var.job_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, { Environment = var.environment })
}

# Baseline Glue permissions (CloudWatch Logs, Glue catalog, etc.)
resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# Scoped access to just the artifacts bucket + the one secret
resource "aws_iam_role_policy" "glue_s3_secrets" {
  name = "${var.job_name}-s3-secrets"
  role = aws_iam_role.glue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadArtifactsBucket"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [var.s3_bucket_arn, "${var.s3_bucket_arn}/*"]
      },
      {
        Sid      = "ReadDbSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.secret_arn]
      }
    ]
  })
}

# Optional VPC network connection, only created if Postgres needs it
resource "aws_glue_connection" "db_vpc" {
  count           = var.enable_vpc_connection ? 1 : 0
  name            = "${var.job_name}-vpc-connection"
  connection_type = "NETWORK"

  physical_connection_requirements {
    subnet_id              = var.subnet_id
    security_group_id_list = var.security_group_ids
    availability_zone      = var.availability_zone
  }
}

resource "aws_glue_job" "metadata_load" {
  name         = var.job_name
  role_arn     = aws_iam_role.glue.arn
  glue_version = "3.0"
  max_capacity = 0.0625 # smallest Python Shell size - these are small reference/audit tables
  timeout      = 30
  max_retries  = 0

  command {
    name            = "pythonshell"
    script_location = var.script_s3_uri
    python_version  = "3.9"
  }

  default_arguments = merge(
    {
      "--S3_INPUT_PATH"                     = var.s3_input_path
      "--SECRET_NAME"                       = var.secret_arn
      "--DDL_S3_PATH"                       = var.ddl_s3_path
      "--RUN_DDL"                           = var.run_ddl ? "true" : "false"
      "--additional-python-modules"        = "psycopg2-binary,pandas"
      "--TempDir"                           = var.temp_dir_s3_uri
      "--enable-continuous-cloudwatch-log" = "true"
    },
    var.tables != "" ? { "--TABLES" = var.tables } : {}
  )

  connections = var.enable_vpc_connection ? [aws_glue_connection.db_vpc[0].name] : null

  tags = merge(var.tags, { Environment = var.environment })
}

resource "aws_glue_trigger" "schedule" {
  count    = var.enable_schedule ? 1 : 0
  name     = "${var.job_name}-schedule"
  type     = "SCHEDULED"
  schedule = var.schedule_cron
  enabled  = true

  actions {
    job_name = aws_glue_job.metadata_load.name
  }

  tags = var.tags
}

output "job_name" {
  value = aws_glue_job.metadata_load.name
}

output "job_arn" {
  value = aws_glue_job.metadata_load.arn
}

output "role_arn" {
  value = aws_iam_role.glue.arn
}
