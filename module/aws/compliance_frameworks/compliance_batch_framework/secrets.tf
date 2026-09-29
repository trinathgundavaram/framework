resource "aws_secretsmanager_secret" "db" {
  name                    = var.rds_secret_name
  description             = "Postgres credentials for the compliance batch framework (${var.environment})"
  recovery_window_in_days = var.environment == "prod" ? 30 : 0

  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id

  secret_string = jsonencode({
    host     = var.db_host
    port     = var.db_port
    dbname   = var.rds_database_name
    username = var.db_username
    password = var.db_password
  })
}
