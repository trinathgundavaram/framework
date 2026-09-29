resource "aws_s3_bucket" "artifacts" {
  bucket        = var.artifacts_bucket
  force_destroy = var.environment != "prod"

  tags = merge(local.tags, { Component = "${var.name_prefix}-artifacts" })
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

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "glue-temp"
    status = "Enabled"
    filter {
      prefix = "${var.artifacts_bucket_key}/temp/"
    }
    expiration {
      days = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts]
}

resource "aws_s3_bucket_object" "python_code" {
  for_each = toset(["glue_framework_entry.py", "glue_job_metadata_load.py", "rds_conn.py"])

  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/python_code/${each.value}"
  source = "${path.module}/code/${each.value}"
  etag   = filemd5("${path.module}/code/${each.value}")

  tags = local.tags
}

resource "aws_s3_bucket_object" "wheelhouse" {
  for_each = local.wheels

  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/wheelhouse/${each.value}"
  source = "${path.module}/dist/wheelhouse/${each.value}"
  etag   = filemd5("${path.module}/dist/wheelhouse/${each.value}")

  tags = local.tags
}

resource "aws_s3_bucket_object" "ddl" {
  count = fileexists("${path.module}/dist/schema.sql") ? 1 : 0

  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/ddl/schema.sql"
  source = "${path.module}/dist/schema.sql"
  etag   = filemd5("${path.module}/dist/schema.sql")

  tags = local.tags
}

resource "aws_s3_bucket_object" "seed_data" {
  for_each = fileset("${path.module}/code/seed_data", "*.csv")

  bucket = aws_s3_bucket.artifacts.id
  key    = "${var.artifacts_bucket_key}/seed_data/${each.value}"
  source = "${path.module}/code/seed_data/${each.value}"
  etag   = filemd5("${path.module}/code/seed_data/${each.value}")

  tags = local.tags
}
