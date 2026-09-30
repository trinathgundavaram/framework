data "aws_region" "current" {}

data "aws_iam_role" "glue_role" {
  name = "${var.name_prefix}_glue_${var.env}"
}

locals {
  tags        = merge(var.required_common_tags, { Environment = var.env })
  common_dir  = "${path.module}/../common"
  code_prefix = "${var.artifacts_bucket_key}/python_code"
  code_uri    = "s3://${var.artifacts_bucket}/${local.code_prefix}"
  temp_dir    = "s3://${var.artifacts_bucket}/${var.artifacts_bucket_key}/tmp/"
  connections = length(var.glue_connection_names) > 0 ? var.glue_connection_names : null

  scripts = toset(["glue_framework_entry.py", "glue_job_metadata_load.py", "rds_conn.py"])
  wheels  = fileset("${local.common_dir}/wheels", "*.whl")
  pypi_packages = join(",", concat(
    [for w in sort(tolist(local.wheels)) : "s3://${var.artifacts_bucket}/${var.artifacts_bucket_key}/wheels/${w}"],
    var.pypi_packages,
  ))

  framework_settings = merge(
    {
      DB_SECRET_NAME        = var.rds_secret_name
      DB_SSLMODE            = "require"
      METADATA_SCHEMA       = var.metadata_schema
      AWS_REGION            = data.aws_region.current.name
      BUSINESS_TZ           = var.business_tz
      OBJECT_STORE          = "s3"
      QUARANTINE_URI        = var.quarantine_uri
      NOTIFY_BACKEND        = "ses"
      NOTIFY_FROM_EMAIL     = var.notify_from_email
      DEFAULT_NOTIFY_EMAILS = join(",", var.default_notify_emails)
      RULE_ENGINE           = var.rule_engine
    },
    var.gre_entrypoint == "" ? {} : { GRE_ENTRYPOINT = var.gre_entrypoint },
    var.rds_database_name == null ? {} : { DB_NAME = var.rds_database_name },
    var.framework_settings,
  )

  common_arguments = {
    "--additional-python-modules"        = local.pypi_packages
    "--env"                              = var.env
    "--region"                           = data.aws_region.current.name
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--job-bookmark-option"              = "job-bookmark-disable"
    "--TempDir"                          = local.temp_dir
  }
}

resource "aws_s3_object" "scripts" {
  for_each = local.scripts

  bucket = var.artifacts_bucket
  key    = "${local.code_prefix}/${each.value}"
  source = "${local.common_dir}/${each.value}"
  etag   = filemd5("${local.common_dir}/${each.value}")
  tags   = local.tags
}

resource "aws_s3_object" "wheels" {
  for_each = local.wheels

  bucket = var.artifacts_bucket
  key    = "${var.artifacts_bucket_key}/wheels/${each.value}"
  source = "${local.common_dir}/wheels/${each.value}"
  etag   = filemd5("${local.common_dir}/wheels/${each.value}")
  tags   = local.tags
}

resource "aws_glue_job" "runner" {
  name                   = "${var.name_prefix}_runner_${var.env}"
  description            = "compliance batch framework: runs one framework module or command per run"
  glue_version           = var.glue_version
  role_arn               = data.aws_iam_role.glue_role.arn
  tags                   = local.tags
  connections            = local.connections
  security_configuration = var.glue_security_conf
  max_capacity           = var.max_capacity
  timeout                = 60
  max_retries            = 0

  execution_property {
    max_concurrent_runs = var.runner_max_concurrent_runs
  }

  command {
    name            = "pythonshell"
    python_version  = var.python_version
    script_location = "${local.code_uri}/glue_framework_entry.py"
  }

  default_arguments = merge(
    local.common_arguments,
    { "--FW_FAIL_ON_EXIT_CODES" = "1,2" },
    { for name, value in local.framework_settings : "--FRAMEWORK_${name}" => value },
  )

  lifecycle {
    precondition {
      condition     = length(local.wheels) > 0
      error_message = "common/wheels has no framework wheel: run ./build_framework_wheel.sh <framework repository>."
    }
  }

  depends_on = [aws_s3_object.scripts, aws_s3_object.wheels]
}

resource "aws_glue_job" "metadata_load" {
  name                   = "${var.name_prefix}_metadata_load_${var.env}"
  description            = "compliance batch framework: upsert one CSV into one configuration table"
  glue_version           = var.glue_version
  role_arn               = data.aws_iam_role.glue_role.arn
  tags                   = local.tags
  connections            = local.connections
  security_configuration = var.glue_security_conf
  max_capacity           = 0.0625
  timeout                = 30
  max_retries            = 0

  execution_property {
    max_concurrent_runs = 1
  }

  command {
    name            = "pythonshell"
    python_version  = var.python_version
    script_location = "${local.code_uri}/glue_job_metadata_load.py"
  }

  default_arguments = merge(
    local.common_arguments,
    {
      "--extra-py-files" = "${local.code_uri}/rds_conn.py"
      "--S3_INPUT_PATH"  = "s3://${var.artifacts_bucket}/${var.artifacts_bucket_key}/seed_data/"
      "--RDS_SECRET_NM"  = var.rds_secret_name
      "--REGION"         = data.aws_region.current.name
    },
    var.rds_database_name == null ? {} : { "--RDS_DATABASE_NM" = var.rds_database_name },
  )

  depends_on = [aws_s3_object.scripts, aws_s3_object.wheels]
}

output "runner_job_name" {
  value = aws_glue_job.runner.name
}

output "metadata_load_job_name" {
  value = aws_glue_job.metadata_load.name
}
