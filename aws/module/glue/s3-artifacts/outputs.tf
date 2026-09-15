output "bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}

output "script_s3_uri" {
  description = "s3:// URI of glue_job_metadata_load.py - pass to the Glue job's ScriptLocation."
  value       = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.glue_script.key}"
}

output "ddl_s3_uri" {
  description = "s3:// URI of create_metadata_tables.sql - the job downloads and applies this DDL."
  value       = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.ddl_script.key}"
}

output "seed_data_s3_uri" {
  description = "s3:// prefix (folder) the job reads <table_key>.csv from."
  value       = "s3://${aws_s3_bucket.artifacts.id}/seed_data/"
}
