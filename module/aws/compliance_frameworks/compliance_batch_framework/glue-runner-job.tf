resource "aws_glue_job" "runner" {
  name         = local.runner_job_name
  description  = "compliance batch framework: runs one framework module or command per run"
  role_arn     = aws_iam_role.glue.arn
  glue_version = "3.0"
  max_capacity = 0.0625
  timeout      = 60
  max_retries  = 0
  connections  = local.connections

  execution_property {
    max_concurrent_runs = var.runner_max_concurrent_runs
  }

  command {
    name            = "pythonshell"
    script_location = "${local.code_uri}/glue_framework_entry.py"
    python_version  = "3.9"
  }

  default_arguments = merge(
    {
      "--FW_FAIL_ON_EXIT_CODES"            = "1,2"
      "--additional-python-modules"        = join(",", local.python_modules)
      "--TempDir"                          = local.temp_dir_uri
      "--enable-continuous-cloudwatch-log" = "true"
      "--enable-metrics"                   = "true"
      "--job-language"                     = "python"
    },
    { for name, value in local.framework_settings : "--FRAMEWORK_${name}" => value },
  )

  tags = local.tags

  lifecycle {
    precondition {
      condition     = length(local.wheels) > 0
      error_message = "dist/wheelhouse is empty: run ./build_artifacts.sh <framework repository> before plan/apply."
    }
  }

  depends_on = [aws_s3_bucket_object.python_code, aws_s3_bucket_object.wheelhouse]
}
