output "job_name" {
  value = aws_glue_job.metadata_load.name
}

output "job_arn" {
  value = aws_glue_job.metadata_load.arn
}

output "role_arn" {
  value = aws_iam_role.glue.arn
}

output "role_name" {
  value = aws_iam_role.glue.name
}
