name_prefix = "compliance_batch_framework"

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
  AppName   = "Compliance"
  ManagedBy = "Terraform"
}
