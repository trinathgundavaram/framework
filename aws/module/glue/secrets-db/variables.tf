variable "create_secret" {
  description = "true = create a new Secrets Manager secret from the db_* inputs below. false = reference an existing secret via existing_secret_arn."
  type        = bool
  default     = true
}

variable "secret_name" {
  description = "Name for the new secret (only used when create_secret = true)."
  type        = string
  default     = ""
}

variable "existing_secret_arn" {
  description = "ARN of an existing Secrets Manager secret (only used when create_secret = false)."
  type        = string
  default     = ""
}

variable "db_host" {
  description = "Postgres host/endpoint."
  type        = string
  default     = ""
}

variable "db_port" {
  description = "Postgres port."
  type        = number
  default     = 5432
}

variable "db_name" {
  description = "Postgres database name."
  type        = string
  default     = ""
}

variable "db_username" {
  description = "Postgres username."
  type        = string
  default     = ""
  sensitive   = true
}

variable "db_password" {
  description = "Postgres password. Pass via an environment variable at apply time - never commit this."
  type        = string
  default     = ""
  sensitive   = true
}

variable "kms_key_id" {
  description = "Optional KMS key id/ARN to encrypt the secret. Leave null for the default aws/secretsmanager key."
  type        = string
  default     = null
}

variable "recovery_window_in_days" {
  description = "Secrets Manager recovery window on delete. 0 disables recovery (immediate delete) - useful in dev, avoid in prod."
  type        = number
  default     = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
