output "secret_arn" {
  value = var.create_secret ? aws_secretsmanager_secret.db[0].arn : data.aws_secretsmanager_secret.existing[0].arn
}

output "secret_name" {
  value = var.create_secret ? aws_secretsmanager_secret.db[0].name : data.aws_secretsmanager_secret.existing[0].name
}
