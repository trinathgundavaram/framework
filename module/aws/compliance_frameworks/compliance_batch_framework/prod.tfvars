environment = "prod"
region      = "us-east-1"

artifacts_bucket = "REPLACE_WITH_PROD_ARTIFACTS_BUCKET_NAME"

rds_secret_name   = "compliance-batch-framework/prod/postgres"
rds_database_name = "REPLACE_WITH_PROD_DB_NAME"
db_host           = "REPLACE_WITH_PROD_DB_HOST"
db_port           = 5432
metadata_schema   = "cms_compliance"

enable_vpc_connection = true
subnet_id             = "REPLACE_WITH_PROD_PRIVATE_SUBNET_ID"
security_group_ids    = ["REPLACE_WITH_PROD_GLUE_SECURITY_GROUP_ID"]
availability_zone     = "REPLACE_WITH_PROD_SUBNET_AZ"

data_bucket_names = ["REPLACE_WITH_PROD_INBOUND_BUCKET"]
quarantine_uri    = "s3://REPLACE_WITH_PROD_INBOUND_BUCKET/quarantine/"
kms_key_arns      = []

business_tz           = "America/Chicago"
notify_from_email     = "REPLACE_WITH_SES_VERIFIED_SENDER@example.com"
default_notify_emails = ["REPLACE_WITH_OPS_EMAIL@example.com"]
rule_engine           = "gre"
gre_entrypoint        = ""
framework_settings    = {}

schedule_timezone = "America/Chicago"
enable_schedules  = true

projects = {
  ODR = {
    alert_emails = ["REPLACE_WITH_ODR_TEAM_EMAIL@example.com"]
    settings     = {}
    schedules = {
      daily_batches = { expression = "cron(0 6 * * ? *)", steps = ["BATCH_CREATION"], run_type = "DAILY", period = "PREV_DAY" }
      file_load     = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close         = { expression = "cron(0 * * * ? *)", steps = ["BATCH_CLOSE", "NOTIFY"] }
    }
  }
  UNIVERSE = {
    alert_emails = ["REPLACE_WITH_UNIVERSE_TEAM_EMAIL@example.com"]
    schedules = {
      daily_batches = { expression = "cron(0 6 * * ? *)", steps = ["BATCH_CREATION"], run_type = "CMS", period = "CURRENT_CALENDAR_MONTH" }
      file_load     = { expression = "cron(5/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close         = { expression = "cron(30 23 * * ? *)", steps = ["BATCH_CLOSE", "NOTIFY"] }
    }
  }
  all_projects = {
    schedules = {
      notify_unscoped = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] }
    }
  }
}

enable_failure_alerts = true
alert_email           = "REPLACE_WITH_ALERT_EMAIL@example.com"

required_common_tags = {
  Environment    = "prod"
  AppName        = "Compliance Batch Framework"
  BusinessEntity = "Compliance"
}
