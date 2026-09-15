include "root" {
  path = find_in_parent_folders("terragrunt.hcl")
}

locals {
  env_vars    = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  environment = local.env_vars.locals.environment
}

terraform {
  source = "../../../../aws/glue//secrets"
}

# Credentials are NEVER hardcoded here - they're read from environment
# variables at apply time:
#   export CMS_DB_HOST_DEV=your-db-host
#   export CMS_DB_USERNAME_DEV=your-db-user
#   export CMS_DB_PASSWORD_DEV=your-db-password
inputs = {
  secret_name = "cms-compliance/dev/postgres"
  environment = local.environment

  db_host     = get_env("CMS_DB_HOST_DEV", "")
  db_port     = 5432
  db_name     = "cms_compliance"
  db_username = get_env("CMS_DB_USERNAME_DEV", "")
  db_password = get_env("CMS_DB_PASSWORD_DEV", "")

  tags = {
    Component = "cms-compliance-db-secret"
  }
}
