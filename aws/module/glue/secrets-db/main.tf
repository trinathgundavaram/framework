##############################################################################
# secrets-db
#
# Postgres connection secret the Glue job reads via SECRET_NAME. Either
# created here from sensitive inputs (create_secret = true, the default), or
# just referenced by ARN when you already manage the secret elsewhere.
##############################################################################

resource "aws_secretsmanager_secret" "db" {
  count       = var.create_secret ? 1 : 0
  name        = var.secret_name
  kms_key_id  = var.kms_key_id
  description = "Postgres credentials for the CMS compliance metadata Glue job"

  recovery_window_in_days = var.recovery_window_in_days

  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "db" {
  count     = var.create_secret ? 1 : 0
  secret_id = aws_secretsmanager_secret.db[0].id

  secret_string = jsonencode({
    host     = var.db_host
    port     = var.db_port
    dbname   = var.db_name
    user     = var.db_username
    password = var.db_password
  })
}

data "aws_secretsmanager_secret" "existing" {
  count = var.create_secret ? 0 : 1
  arn   = var.existing_secret_arn
}
