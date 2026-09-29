output "runner_job_name" {
  description = "The Glue job every workflow step (and every manual command) runs."
  value       = aws_glue_job.runner.name
}

output "metadata_load_job_name" {
  value = aws_glue_job.metadata_load.name
}

output "workflows" {
  description = "Step Functions workflow per project (and all_projects): name and ARN."
  value       = { for k, m in aws_sfn_state_machine.workflow : k => { name = m.name, arn = m.arn } }
}

output "schedule_groups" {
  value = { for k, g in aws_scheduler_schedule_group.workflow : k => g.name }
}

output "schedule_dlq_url" {
  value = aws_sqs_queue.schedule_dlq.url
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "seed_data_uri" {
  value = local.seed_data_uri
}

output "secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}

output "glue_role_arn" {
  value = aws_iam_role.glue.arn
}
