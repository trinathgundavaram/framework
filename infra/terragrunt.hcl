##############################################################################
# Root Terragrunt config.
#
# Every component's terragrunt.hcl does:
#     include "root" { path = find_in_parent_folders("terragrunt.hcl") }
# which pulls in the remote_state backend + provider generated here. The
# actual bucket/region/lock-table values come from each environment's
# env.hcl (infra/<env>/env.hcl) - fill those in with your existing state
# bucket + DynamoDB lock table names for dev / test / prod.
##############################################################################

locals {
  env_vars    = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  environment = local.env_vars.locals.environment
  aws_region  = local.env_vars.locals.aws_region
  state_bucket = local.env_vars.locals.state_bucket
  lock_table   = local.env_vars.locals.lock_table
}

remote_state {
  backend = "s3"

  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }

  config = {
    bucket         = local.state_bucket
    key            = "${path_relative_to_include()}/terraform.tfstate"
    region         = local.aws_region
    dynamodb_table = local.lock_table
    encrypt        = true
  }
}

generate "provider" {
  path      = "provider.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    provider "aws" {
      region = "${local.aws_region}"

      default_tags {
        tags = {
          Project     = "cms-compliance-framework"
          Environment = "${local.environment}"
          ManagedBy   = "terragrunt"
        }
      }
    }
  EOF
}

generate "versions" {
  path      = "versions.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<-EOF
    terraform {
      required_version = ">= 1.5.0"
      required_providers {
        aws = {
          source  = "hashicorp/aws"
          version = "~> 5.0"
        }
      }
    }
  EOF
}

# Note: no blanket `inputs` block here on purpose - each module declares a
# different variable set, so environment/region are passed explicitly by
# each component's own terragrunt.hcl instead of merged in globally.
