##############################################################################
# glue-metadata-job
#
# The Glue Python Shell job (glue_job_metadata_load.py) that loads / upserts
# all 5 CMS compliance metadata tables into Postgres, reading its script and
# input CSVs from S3 (s3-artifacts module) and its DB credentials from
# Secrets Manager (secrets-db module).
##############################################################################

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

  tags = var.tags
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
  glue_version = var.glue_version
  max_capacity = var.max_capacity
  timeout      = var.timeout_minutes
  max_retries  = var.max_retries

  command {
    name            = "pythonshell"
    script_location = var.script_s3_uri
    python_version  = var.python_version
  }

  default_arguments = merge(
    {
      "--S3_INPUT_PATH"              = var.s3_input_path
      "--SECRET_NAME"                = var.secret_arn
      "--DDL_S3_PATH"                = var.ddl_s3_path
      "--RUN_DDL"                    = var.run_ddl ? "true" : "false"
      "--additional-python-modules" = var.additional_python_modules
      "--TempDir"                    = var.temp_dir_s3_uri
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
