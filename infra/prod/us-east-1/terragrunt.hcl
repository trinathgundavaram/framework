include "root" {
  path = find_in_parent_folders("terragrunt.hcl")
}

terraform {
  source = "../../../module/aws/compliance_frameworks/compliance_batch_framework"

  extra_arguments "env_tfvars" {
    commands  = get_terraform_commands_that_need_vars()
    arguments = ["-var-file=${get_terragrunt_dir()}/../../../module/aws/compliance_frameworks/compliance_batch_framework/prod.tfvars"]
  }
}
