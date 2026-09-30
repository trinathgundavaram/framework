name_prefix = "compliance_batch_framework"

artifacts_bucket     = "silverton-maa-global-artifactory-test"
artifacts_bucket_key = "compliance_batch_framework"

rds_secret_name   = "REPLACE_WITH_TEST_RDS_SECRET_NAME"
rds_database_name = null
metadata_schema   = "cms_compliance"

glue_version          = "3.0"
python_version        = "3.9"
glue_security_conf    = null
glue_connection_names = ["REPLACE_WITH_TEST_GLUE_RDS_CONNECTION"]
pypi_packages         = ["psycopg[binary]==3.2.13", "tzdata==2026.4", "pg8000==1.31.5"]

data_bucket_names = ["REPLACE_WITH_TEST_INBOUND_BUCKET"]
quarantine_uri    = "s3://REPLACE_WITH_TEST_INBOUND_BUCKET/quarantine/"
kms_key_arns      = []

business_tz           = "America/Chicago"
notify_from_email     = "REPLACE_WITH_SES_VERIFIED_SENDER@example.com"
default_notify_emails = ["REPLACE_WITH_OPS_EMAIL@example.com"]
rule_engine           = "gre"
gre_entrypoint        = ""
framework_settings    = {}

enable_schedules     = true
enable_workflow_logs = false

# Schedule times are UTC (EventBridge rules). Chicago = UTC-5 (CDT, Mar-Nov) / UTC-6 (CST, Nov-Mar):
#   cron(0 12 ...)  -> 07:00 CDT / 06:00 CST  (never before 06:00 local)
#   cron(30 4 ...)  -> 23:30 CDT / 22:30 CST  (always the same local day)
projects = {
  ODR = {
    alert_emails = ["REPLACE_WITH_ODR_TEAM_EMAIL@example.com"]
    settings     = {}
    schedules = {
      daily_batches = { expression = "cron(0 12 * * ? *)", steps = ["BATCH_CREATION"], run_type = "DAILY", period = "PREV_DAY" }
      file_load     = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close         = { expression = "cron(0 * * * ? *)", steps = ["BATCH_CLOSE", "NOTIFY"] }
    }
  }
  UNIVERSE = {
    alert_emails = ["REPLACE_WITH_UNIVERSE_TEAM_EMAIL@example.com"]
    schedules = {
      daily_batches = { expression = "cron(0 12 * * ? *)", steps = ["BATCH_CREATION"], run_type = "CMS", period = "CURRENT_CALENDAR_MONTH" }
      file_load     = { expression = "cron(5/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close         = { expression = "cron(30 4 * * ? *)", steps = ["BATCH_CLOSE", "NOTIFY"] }
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

permissions_boundary = null

required_common_tags = {
  AppName        = "Compliance Batch Framework"
  BusinessEntity = "Compliance"
}
