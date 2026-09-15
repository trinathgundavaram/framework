##############################################################################
# dev - CMS Compliance Framework metadata Glue job
#
# One Terragrunt component wrapping aws/glue (single specific Terraform
# config, applied as-is for every environment). Environment-specific values
# come from the .tfvars file co-located with the .tf files
# (aws/glue/dev.tfvars) rather than from Terragrunt `inputs`.
##############################################################################

include "root" {
  path = find_in_parent_folders("terragrunt.hcl")
}

terraform {
  source = "../../../aws/glue"

  extra_arguments "env_tfvars" {
    commands  = get_terraform_commands_that_need_vars()
    arguments = ["-var-file=${get_terragrunt_dir()}/../../../aws/glue/dev.tfvars"]
  }
}
