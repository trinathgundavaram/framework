include "root" {
  path = find_in_parent_folders("terragrunt.hcl")
}

locals {
  env_vars    = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  environment = local.env_vars.locals.environment
}

terraform {
  source = "../../../../aws/module/glue//secrets-db"
}

# Credentials are NEVER hardcoded here - they're read from environment
# variables at apply time:
#   export CMS_DB_HOST_TEST=your-db-host
#   export CMS_DB_USERNAME_TEST=your-db-user
#   export CMS_DB_PASSWORD_TEST=your-db-password
# (or export TF_VAR_db_password etc. and reference var.db_password directly -
# get_env() here is just one convenient way to plumb them through Terragrunt.)
inputs = {
  create_secret = true
  secret_name   = "cms-compliance/test/postgres"

  db_host     = get_env("CMS_DB_HOST_TEST", "")
  db_port     = 5432
  db_name     = "cms_compliance"
  db_username = get_env("CMS_DB_USERNAME_TEST", "")
  db_password = get_env("CMS_DB_PASSWORD_TEST", "")

  recovery_window_in_days = local.environment == "prod" ? 30 : 0

  tags = {
    Component = "cms-compliance-db-secret"
  }
}
