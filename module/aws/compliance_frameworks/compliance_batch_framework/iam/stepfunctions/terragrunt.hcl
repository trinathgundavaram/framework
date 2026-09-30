include "root" {
  path = find_in_parent_folders()
}

terraform {
  extra_arguments "env_tfvars" {
    commands  = get_terraform_commands_that_need_vars()
    arguments = ["-var-file=${find_in_parent_folders("config/${get_env("TF_VAR_env")}.tfvars")}"]
  }
}
