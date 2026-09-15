include "root" {
  path = find_in_parent_folders("terragrunt.hcl")
}

locals {
  env_vars    = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  environment = local.env_vars.locals.environment
}

terraform {
  source = "../../../../aws/glue//s3-artifacts"
}

inputs = {
  bucket_name = "cms-compliance-glue-artifacts-${local.environment}"
  environment = local.environment

  # Local sources uploaded to S3 - path is relative to the repo root
  # (infra/terragrunt.hcl's directory, one level up from infra/).
  glue_script_path = "${get_parent_terragrunt_dir()}/../aws/glue/app/glue_job_metadata_load.py"
  ddl_script_path  = "${get_parent_terragrunt_dir()}/../aws/glue/app/ddl/create_metadata_tables.sql"
  seed_data_dir    = "${get_parent_terragrunt_dir()}/../aws/glue/app/seed_data"

  tags = {
    Component = "cms-compliance-glue-artifacts"
  }
}
