##############################################################################
# s3-artifacts
#
# Bucket that holds everything the Glue metadata-load job reads:
#   scripts/glue_job_metadata_load.py
#   ddl/create_metadata_tables.sql
#   seed_data/<table>.csv   (one-time seed + refreshed for later manual updates)
##############################################################################

resource "aws_s3_bucket" "artifacts" {
  bucket        = var.bucket_name
  force_destroy = var.force_destroy

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
      sse_algorithm     = var.kms_key_arn != null ? "aws:kms" : "AES256"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = var.kms_key_arn != null
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
