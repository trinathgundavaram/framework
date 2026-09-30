resource "aws_s3_object" "scripts" {
  for_each = local.scripts

  bucket = var.artifacts_bucket
  key    = "${local.code_prefix}/${each.value}"
  source = "${local.code_dir}/${each.value}"
  etag   = filemd5("${local.code_dir}/${each.value}")
  tags   = local.tags
}

resource "aws_s3_object" "wheels" {
  for_each = local.wheels

  bucket = var.artifacts_bucket
  key    = "${var.artifacts_bucket_key}/wheels/${each.value}"
  source = "${local.code_dir}/wheels/${each.value}"
  etag   = filemd5("${local.code_dir}/wheels/${each.value}")
  tags   = local.tags
}

resource "aws_glue_job" "runner" {
  name                   = "${var.name_prefix}_runner_${var.env}"
  description            = "compliance batch framework: runs one framework module or command per run"
  glue_version           = var.glue_version
  role_arn               = aws_iam_role.glue.arn
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
    { "--FW_FAIL_ON_EXIT_CODES" = "1,2", "--extra-py-files" = join(",", local.wheel_uris) },
    { for name, value in local.framework_settings : "--FRAMEWORK_${name}" => value },
  )

  lifecycle {
    precondition {
      condition     = length(local.wheels) > 0
      error_message = "code/wheels has no framework wheel: run scripts/build_glue_wheel.sh in the framework repository."
    }
  }

  depends_on = [aws_s3_object.scripts, aws_s3_object.wheels]
}

resource "aws_glue_job" "metadata_load" {
  name                   = "${var.name_prefix}_metadata_load_${var.env}"
  description            = "compliance batch framework: upsert one CSV into one configuration table"
  glue_version           = var.glue_version
  role_arn               = aws_iam_role.glue.arn
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
      "--S3_INPUT_PATH"  = "s3://${var.artifacts_bucket}/${var.artifacts_bucket_key}/config_data/"
      "--RDS_SECRET_NM"  = var.rds_secret_name
      "--REGION"         = local.region
    },
    var.rds_database_name == null ? {} : { "--RDS_DATABASE_NM" = var.rds_database_name },
  )

  depends_on = [aws_s3_object.scripts, aws_s3_object.wheels]
}
