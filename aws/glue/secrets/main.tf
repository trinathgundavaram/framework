##############################################################################
# CMS Compliance Framework - Postgres credentials for the Glue metadata job
#
# Specific to this deployment (dev/test/prod via Terragrunt inputs) - always
# creates the secret; if you manage this secret elsewhere, don't apply this
# component and pass its ARN directly into glue-job's secret_arn input instead.
##############################################################################

variable "environment" {
  type = string
}

variable "secret_name" {
  type = string
}

variable "db_host" {
  type = string
}

variable "db_port" {
  type    = number
  default = 5432
}

variable "db_name" {
  type = string
}

variable "db_username" {
  type      = string
  sensitive = true
}

variable "db_password" {
  description = "Pass via an environment variable at apply time - never commit this."
  type        = string
  sensitive   = true
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  # Recoverable delete window: skip in dev/test, protect prod.
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
}

resource "aws_secretsmanager_secret" "db" {
  name                    = var.secret_name
  description             = "Postgres credentials for the CMS compliance metadata Glue job (${var.environment})"
  recovery_window_in_days = local.recovery_window_in_days

  tags = merge(var.tags, { Environment = var.environment })
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id

  secret_string = jsonencode({
    host     = var.db_host
    port     = var.db_port
    dbname   = var.db_name
    user     = var.db_username
    password = var.db_password
  })
}

output "secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}

output "secret_name" {
  value = aws_secretsmanager_secret.db.name
}
