mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_region" { defaults = { name = "us-east-1" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/r" } }
}

variables {
  env = "dev"
}

run "jobs" {
  command = apply

  assert {
    condition     = aws_glue_job.runner.name == "compliance_batch_framework_runner_dev" && aws_glue_job.runner.execution_property[0].max_concurrent_runs == 20
    error_message = "runner job"
  }
  assert {
    condition     = startswith(aws_glue_job.runner.default_arguments["--additional-python-modules"], "s3://silverton-maa-global-artifactory-dev/compliance_batch_framework/wheels/cms_compliance_framework-") && strcontains(aws_glue_job.runner.default_arguments["--additional-python-modules"], ",psycopg[binary]==")
    error_message = "framework wheel from S3, dependencies from PyPI: ${aws_glue_job.runner.default_arguments["--additional-python-modules"]}"
  }
  assert {
    condition     = aws_glue_job.metadata_load.default_arguments["--additional-python-modules"] == aws_glue_job.runner.default_arguments["--additional-python-modules"]
    error_message = "both jobs install the same packages"
  }
  assert {
    condition     = aws_glue_job.runner.default_arguments["--FRAMEWORK_DB_SSLMODE"] == "require" && !contains(keys(aws_glue_job.runner.default_arguments), "--FRAMEWORK_DB_NAME") && !contains(keys(aws_glue_job.metadata_load.default_arguments), "--RDS_DATABASE_NM")
    error_message = "SSL required; the database name comes from the secret unless rds_database_name is set"
  }
  assert {
    condition     = aws_iam_role.glue.name == "compliance_batch_framework_glue_dev" && aws_glue_job.runner.role_arn == aws_iam_role.glue.arn
    error_message = "the jobs run as the module's Glue role"
  }
  assert {
    condition     = aws_glue_job.metadata_load.default_arguments["--S3_INPUT_PATH"] == "s3://silverton-maa-global-artifactory-dev/compliance_batch_framework/config_data/"
    error_message = "metadata_load reads configuration CSVs from config_data/"
  }
  assert {
    condition     = aws_glue_job.runner.connections == tolist(["REPLACE_WITH_DEV_GLUE_RDS_CONNECTION"]) && aws_glue_job.runner.command[0].script_location == "s3://silverton-maa-global-artifactory-dev/compliance_batch_framework/python_code/glue_framework_entry.py"
    error_message = "existing Glue connection and uploaded script"
  }
}

run "explicit_database_name" {
  command = apply
  variables {
    rds_database_name = "compliance"
  }
  assert {
    condition     = aws_glue_job.runner.default_arguments["--FRAMEWORK_DB_NAME"] == "compliance" && aws_glue_job.metadata_load.default_arguments["--RDS_DATABASE_NM"] == "compliance"
    error_message = "rds_database_name reaches both jobs"
  }
}
