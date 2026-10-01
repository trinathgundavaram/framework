resource "aws_s3_object" "scripts" {
  for_each = local.glue_scripts

  bucket = local.artifacts_bucket
  key    = "${local.glue_script_prefix}/${each.value}"
  source = "${local.code_dir}/${each.value}"
  etag   = filemd5("${local.code_dir}/${each.value}")
  tags   = local.tags
}

resource "aws_s3_object" "wheels" {
  for_each = local.wheels

  bucket = local.artifacts_bucket
  key    = "${local.wheel_prefix}/${each.value}"
  source = "${local.code_dir}/wheels/${each.value}"
  etag   = filemd5("${local.code_dir}/wheels/${each.value}")
  tags   = local.tags
}

resource "aws_glue_job" "runner" {
  name                   = local.runner_job_name
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
    max_concurrent_runs = local.runner_max_concurrent_runs
  }

  command {
    name            = "pythonshell"
    python_version  = var.python_version
    script_location = "s3://${local.artifacts_bucket}/${local.runner_script_key}"
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
  name                   = local.metadata_load_job_name
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
    script_location = "s3://${local.artifacts_bucket}/${local.metadata_script_key}"
  }

  default_arguments = merge(
    local.common_arguments,
    {
      "--extra-py-files" = "s3://${local.artifacts_bucket}/${local.rds_conn_script_key}"
      "--S3_INPUT_PATH"  = "s3://${local.artifacts_bucket}/${local.config_data_prefix}/"
      "--RDS_SECRET_NM"  = var.rds_secret_name
      "--REGION"         = local.region
    },
    var.rds_database_name == null ? {} : { "--RDS_DATABASE_NM" = var.rds_database_name },
  )

  depends_on = [aws_s3_object.scripts, aws_s3_object.wheels]
}
