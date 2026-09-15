##############################################################################
# CMS Compliance Framework - Postgres credentials for the Glue metadata job
#
# JSON body keys (host/port/dbname/username/password) match what
# code/rds_conn.py's RdsClient.set_rds_connection_details() reads.
##############################################################################

resource "aws_secretsmanager_secret" "db" {
  name                    = var.rds_secret_name
  description             = "Postgres credentials for the CMS compliance metadata Glue job (${var.environment})"
  recovery_window_in_days = var.environment == "prod" ? 30 : 0

  tags = merge(var.required_common_tags, { Environment = var.environment })
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

output "secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}
