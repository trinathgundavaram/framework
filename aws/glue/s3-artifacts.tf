##############################################################################
# CMS Compliance Framework - Glue metadata-load artifacts bucket
#
# Uses aws_s3_bucket_object (same resource type as the team's
# glue-jobs/glue_file_ingestion.tf glue_stage_code upload) rather than the
# newer aws_s3_object, to match convention. Holds:
#   python_code/glue_job_metadata_load.py
#   python_code/rds_conn.py
#   ddl/schema.sql          (copy of src/framework/sql/schema.sql; reference only - tables are
#                            created by `framework init-db`; the job never applies it)
#   seed_data/<table>.csv   (one-time seed + refreshed for later manual updates)
#
# No config file is uploaded - the job takes --TABLE_NAME/--S3_FILE_NAME/
# --PRIMARY_KEY etc. directly as job parameters, passed manually per run
# (console "Run job" or `aws glue start-job-run --arguments`). See the
# README for the exact commands per table.
##############################################################################

resource "aws_s3_bucket" "artifacts" {
  bucket        = var.artifacts_bucket
  force_destroy = var.environment != "prod"

  tags = merge(var.required_common_tags, {
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

# --- Glue job scripts --------------------------------------------------------
resource "aws_s3_bucket_object" "glue_main_script" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/python_code/glue_job_metadata_load.py"
  source = "${path.module}/code/glue_job_metadata_load.py"
  etag   = filemd5("${path.module}/code/glue_job_metadata_load.py")

  tags = var.required_common_tags
}

resource "aws_s3_bucket_object" "rds_conn_module" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/python_code/rds_conn.py"
  source = "${path.module}/code/rds_conn.py"
  etag   = filemd5("${path.module}/code/rds_conn.py")

  tags = var.required_common_tags
}

# --- DDL (reference only - applied by `framework init-db`; this job never runs it) ---
# Single source of truth: the framework package's schema file.
resource "aws_s3_bucket_object" "ddl_script" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/ddl/schema.sql"
  source = "${path.module}/../../src/framework/sql/schema.sql"
  etag   = filemd5("${path.module}/../../src/framework/sql/schema.sql")

  tags = var.required_common_tags
}

# --- One-time seed / ad-hoc update CSVs, one per metadata table -------------
resource "aws_s3_bucket_object" "seed_data" {
  for_each = fileset("${path.module}/code/seed_data", "*.csv")

  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/seed_data/${each.value}"
  source = "${path.module}/code/seed_data/${each.value}"
  etag   = filemd5("${path.module}/code/seed_data/${each.value}")

  tags = var.required_common_tags
}

locals {
  script_s3_uri    = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_bucket_object.glue_main_script.key}"
  rds_conn_s3_uri  = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_bucket_object.rds_conn_module.key}"
  ddl_s3_uri       = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_bucket_object.ddl_script.key}"
  seed_data_s3_uri = "s3://${aws_s3_bucket.artifacts.id}/${var.artifacts_bucket_key}/seed_data/"
  temp_dir_s3_uri  = "s3://${aws_s3_bucket.artifacts.id}/${var.artifacts_bucket_key}/temp/"
}

output "bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "script_s3_uri" {
  value = local.script_s3_uri
}
