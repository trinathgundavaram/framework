include "root" {
  path = find_in_parent_folders()
}

locals {
  env_config = "${get_terragrunt_dir()}/env-config/${get_env("TF_VAR_region", "us-east-1")}"
}

terraform {
  extra_arguments "env_tfvars" {
    commands  = get_terraform_commands_that_need_vars()
    arguments = [
      "-var-file=${local.env_config}/common.tfvars",
      "-var-file=${local.env_config}/${get_env("TF_VAR_env")}.tfvars",
    ]
  }
}
