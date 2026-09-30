output "runner_job_name" {
  value = aws_glue_job.runner.name
}

output "metadata_load_job_name" {
  value = aws_glue_job.metadata_load.name
}
