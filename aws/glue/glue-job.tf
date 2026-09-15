##############################################################################
# CMS Compliance Framework - metadata-load AWS Glue Python Shell job
#
# Structured like the team's other Glue job Terraform
# (module/aws/part c odr/glue-jobs/glue_file_ingestion.tf): the S3 script
# object + aws_glue_job + SNS failure topic + CloudWatch failure rule all
# live together in one file. Specific to this deployment - not a generic
# reusable module.
##############################################################################

resource "aws_iam_role" "glue" {
  name = var.glue_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(var.required_common_tags, { Environment = var.environment })
}

# Baseline Glue permissions (CloudWatch Logs, Glue catalog, etc.)
resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# Scoped access to just the artifacts bucket + the one secret
resource "aws_iam_role_policy" "glue_s3_secrets" {
  name = "${var.job_name}-s3-secrets"
  role = aws_iam_role.glue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadArtifactsBucket"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      },
      {
        Sid      = "ReadDbSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.db.arn]
      }
    ]
  })
}

# Optional VPC network connection, only created if Postgres needs it
resource "aws_glue_connection" "db_vpc" {
  count           = var.enable_vpc_connection ? 1 : 0
  name            = "${var.job_name}-vpc-connection"
  connection_type = "NETWORK"

  physical_connection_requirements {
    subnet_id              = var.subnet_id
    security_group_id_list = var.security_group_ids
    availability_zone      = var.availability_zone
  }
}

resource "aws_glue_job" "metadata_load" {
  name         = var.job_name
  role_arn     = aws_iam_role.glue.arn
  glue_version = "3.0"
  max_capacity = 0.0625 # smallest Python Shell size - these are small reference/audit tables
  timeout      = 30
  max_retries  = 0

  connections = var.enable_vpc_connection ? [aws_glue_connection.db_vpc[0].name] : null

  execution_property {
    max_concurrent_runs = 1
  }

  command {
    name            = "pythonshell"
    script_location = local.script_s3_uri
    python_version  = "3.9"
  }

  # Only the environment-fixed arguments get a default here. --TABLE_NAME,
  # --S3_FILE_NAME, and --PRIMARY_KEY (and optionally --MODE /
  # --AUDIT_COLUMNS) are NOT set as defaults on purpose - this job loads one
  # table per run, and which table/file/PK that is gets passed manually on
  # every run (console "Run job" -> Job parameters, or `aws glue
  # start-job-run --arguments`). --S3_INPUT_PATH defaults to the seed_data
  # folder but can be overridden per run too if a file lives elsewhere.
  default_arguments = {
    "--S3_INPUT_PATH"                    = local.seed_data_s3_uri
    "--RDS_SECRET_NM"                    = aws_secretsmanager_secret.db.name
    "--RDS_DATABASE_NM"                  = var.rds_database_name
    "--REGION"                           = var.region
    "--extra-py-files"                   = local.rds_conn_s3_uri
    "--additional-python-modules"        = "pg8000,pandas"
    "--TempDir"                          = local.temp_dir_s3_uri
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--job-language"                     = "python"
  }

  tags = merge(var.required_common_tags, { Environment = var.environment })
}

resource "aws_glue_trigger" "schedule" {
  count    = var.enable_schedule ? 1 : 0
  name     = "${var.job_name}-schedule"
  type     = "SCHEDULED"
  schedule = var.schedule_cron
  enabled  = true

  actions {
    job_name = aws_glue_job.metadata_load.name
  }

  tags = var.required_common_tags
}

# --- Failure alerting: SNS topic + CloudWatch event rule, same pattern as ---
# --- the team's other Glue jobs (glue_file_ingestion.tf) -------------------

resource "aws_sns_topic" "email" {
  count = var.enable_failure_alerts ? 1 : 0
  name  = "${var.job_name}-failure-email"

  tags = var.required_common_tags
}

resource "aws_sns_topic_subscription" "email_alert" {
  count     = var.enable_failure_alerts && var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.email[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_event_rule" "glue_failure_rule" {
  count       = var.enable_failure_alerts ? 1 : 0
  name        = "${var.job_name}-failure"
  description = "CMS compliance metadata Glue job has failed"

  event_pattern = jsonencode({
    "source"      = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    "detail" = {
      "jobName" = [aws_glue_job.metadata_load.name]
      "state"   = ["FAILED"]
    }
  })

  tags = var.required_common_tags
}

resource "aws_cloudwatch_event_target" "glue_failure_target" {
  count     = var.enable_failure_alerts ? 1 : 0
  rule      = aws_cloudwatch_event_rule.glue_failure_rule[0].name
  target_id = "send-sns"
  arn       = aws_sns_topic.email[0].arn
}

resource "aws_sns_topic_policy" "glue_failure_policy" {
  count = var.enable_failure_alerts ? 1 : 0
  arn   = aws_sns_topic.email[0].arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.email[0].arn
      }
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "glue_executions_failed" {
  count               = var.enable_failure_alerts ? 1 : 0
  alarm_name          = "${var.job_name}-executions-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "glue.driver.aggregate.numFailedTasks"
  namespace           = "Glue"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "CMS compliance metadata Glue job reported a failed execution"
  alarm_actions       = [aws_sns_topic.email[0].arn]

  dimensions = {
    JobName = aws_glue_job.metadata_load.name
    JobRunId = "ALL"
    Type     = "gauge"
  }

  tags = var.required_common_tags
}

output "job_name" {
  value = aws_glue_job.metadata_load.name
}

output "job_arn" {
  value = aws_glue_job.metadata_load.arn
}

output "role_arn" {
  value = aws_iam_role.glue.arn
}
