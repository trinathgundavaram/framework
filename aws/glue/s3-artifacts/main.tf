##############################################################################
# CMS Compliance Framework - Glue metadata-load artifacts bucket
#
# Specific to this deployment (dev/test/prod via Terragrunt inputs) - not a
# generic reusable module. Holds:
#   scripts/glue_job_metadata_load.py
#   ddl/create_metadata_tables.sql
#   seed_data/<table>.csv   (one-time seed + refreshed for later manual updates)
##############################################################################

variable "bucket_name" {
  description = "Name of the S3 bucket that holds the Glue script, DDL, and seed CSVs. Must be globally unique."
  type        = string
}

variable "environment" {
  description = "dev / test / prod - drives force_destroy and tags."
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

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  # Only prod is protected from accidental destroy.
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = var.bucket_name
  force_destroy = local.force_destroy

  tags = merge(var.tags, {
    Environment = var.environment
    Component   = "cms-compliance-glue-artifacts"
  })
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Glue job script -------------------------------------------------------
resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "scripts/glue_job_metadata_load.py"
  source = var.glue_script_path
  etag   = filemd5(var.glue_script_path)

  tags = var.tags
}

# --- DDL ---------------------------------------------------------------------
resource "aws_s3_object" "ddl_script" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "ddl/create_metadata_tables.sql"
  source = var.ddl_script_path
  etag   = filemd5(var.ddl_script_path)

  tags = var.tags
}

# --- One-time seed / ad-hoc update CSVs, one per metadata table ------------
resource "aws_s3_object" "seed_data" {
  for_each = fileset(var.seed_data_dir, "*.csv")

  bucket = aws_s3_bucket.artifacts.id
  key    = "seed_data/${each.value}"
  source = "${var.seed_data_dir}/${each.value}"
  etag   = filemd5("${var.seed_data_dir}/${each.value}")

  tags = var.tags
}

output "bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}

output "script_s3_uri" {
  value = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.glue_script.key}"
}

output "ddl_s3_uri" {
  value = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.ddl_script.key}"
}

output "seed_data_s3_uri" {
  value = "s3://${aws_s3_bucket.artifacts.id}/seed_data/"
}
