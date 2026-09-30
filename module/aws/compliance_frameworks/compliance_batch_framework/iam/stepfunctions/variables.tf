variable "env" {
  type = string
}

variable "name_prefix" {
  description = "Start of every resource name; must start with compliance (the deployer role only manages compliance* resources)."
  type        = string
  default     = "compliance_batch_framework"

  validation {
    condition     = can(regex("^compliance[a-z0-9_]*$", var.name_prefix))
    error_message = "name_prefix must start with compliance and use lower-case letters, digits or _."
  }
}

variable "required_common_tags" {
  type    = map(string)
  default = {}
}

variable "permissions_boundary" {
  description = "Permissions boundary ARN for the roles, if the account requires one."
  type        = string
  default     = null
}
