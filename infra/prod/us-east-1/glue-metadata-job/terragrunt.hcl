include "root" {
  path = find_in_parent_folders("terragrunt.hcl")
}

locals {
  env_vars    = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  environment = local.env_vars.locals.environment
}

terraform {
  source = "../../../../aws/module/glue//glue-metadata-job"
}

dependency "s3_artifacts" {
  config_path = "../s3-artifacts"

  mock_outputs = {
    bucket_name      = "mock-bucket"
    bucket_arn       = "arn:aws:s3:::mock-bucket"
    script_s3_uri    = "s3://mock-bucket/scripts/glue_job_metadata_load.py"
    ddl_s3_uri       = "s3://mock-bucket/ddl/create_metadata_tables.sql"
    seed_data_s3_uri = "s3://mock-bucket/seed_data/"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init"]
}

dependency "secrets_db" {
  config_path = "../secrets-db"

  mock_outputs = {
    secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:mock-XXXXXX"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan", "init"]
}

inputs = {
  job_name    = "cms-compliance-metadata-load-${local.environment}"
  environment = local.environment

  script_s3_uri  = dependency.s3_artifacts.outputs.script_s3_uri
  ddl_s3_path    = dependency.s3_artifacts.outputs.ddl_s3_uri
  s3_input_path  = dependency.s3_artifacts.outputs.seed_data_s3_uri
  s3_bucket_arn  = dependency.s3_artifacts.outputs.bucket_arn
  temp_dir_s3_uri = "s3://${dependency.s3_artifacts.outputs.bucket_name}/glue-temp/"

  secret_arn = dependency.secrets_db.outputs.secret_arn

  run_ddl = true
  tables  = "" # empty = all 5 tables; e.g. "compliance_request_intake" to reload just one

  # Flip to true + fill in subnet/security-group/AZ if Postgres is private/VPC-only
  enable_vpc_connection = false
  subnet_id              = null
  security_group_ids     = []
  availability_zone       = null

  # Flip to true + set a cron to run updates on a schedule instead of on-demand only
  enable_schedule = false
  schedule_cron   = ""

  tags = {
    Component = "cms-compliance-metadata-glue-job"
  }
}
