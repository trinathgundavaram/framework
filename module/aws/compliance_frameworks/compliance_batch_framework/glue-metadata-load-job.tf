resource "aws_glue_job" "metadata_load" {
  name         = "${var.name_prefix}_metadata_load_${var.environment}"
  description  = "compliance batch framework: upsert one seed CSV into one table"
  role_arn     = aws_iam_role.glue.arn
  glue_version = "3.0"
  max_capacity = 0.0625
  timeout      = 30
  max_retries  = 0
  connections  = local.connections

  execution_property {
    max_concurrent_runs = 1
  }

  command {
    name            = "pythonshell"
    script_location = "${local.code_uri}/glue_job_metadata_load.py"
    python_version  = "3.9"
  }

  default_arguments = {
    "--S3_INPUT_PATH"                    = local.seed_data_uri
    "--RDS_SECRET_NM"                    = aws_secretsmanager_secret.db.name
    "--RDS_DATABASE_NM"                  = var.rds_database_name
    "--REGION"                           = var.region
    "--extra-py-files"                   = "${local.code_uri}/rds_conn.py"
    "--additional-python-modules"        = join(",", local.python_modules)
    "--TempDir"                          = local.temp_dir_uri
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--job-language"                     = "python"
  }

  tags = local.tags

  depends_on = [aws_s3_bucket_object.python_code, aws_s3_bucket_object.wheelhouse]
}
