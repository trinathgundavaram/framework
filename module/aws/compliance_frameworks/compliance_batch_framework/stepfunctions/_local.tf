locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  name_prefix     = "compliance_batch_framework"
  runner_job_name = "${local.name_prefix}_runner_${var.env}"
  known_steps     = sort(keys(var.step_settings))

  tags = merge(var.required_common_tags, {
    Environment = var.env
    ManagedBy   = "Terraform"
  })

  # FILE_RULES runs the rules on files already loaded to core, in its own workflow execution after the load.
  # Turn it on once the Glue runner has a rules engine (glue/_local.tf: rule_engine / gre_entrypoint).
  file_rules_enabled = false

  # One entry per project (key = Project_Cd); all_projects runs its steps without --project.
  # Schedule times are UTC: cron(0 12 ...) = 07:00 CDT / 06:00 CST, cron(30 4 ...) = 23:30 CDT / 22:30 CST.
  projects = {
    ODR = {
      schedules = {
        daily_batches = { expression = "cron(0 12 * * ? *)", steps = ["BATCH_CREATION"], run_type = "DAILY", period = "PREV_DAY" }
        file_load     = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
        file_rules    = { expression = "cron(10/15 * * * ? *)", steps = ["FILE_RULES", "NOTIFY"], enabled = local.file_rules_enabled }
        close         = { expression = "cron(0 * * * ? *)", steps = ["BATCH_CLOSE", "NOTIFY"] }
      }
    }
    UNIVERSE = {
      schedules = {
        daily_batches = { expression = "cron(0 12 * * ? *)", steps = ["BATCH_CREATION"], run_type = "CMS", period = "CURRENT_CALENDAR_MONTH" }
        file_load     = { expression = "cron(5/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
        file_rules    = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_RULES", "NOTIFY"], enabled = local.file_rules_enabled }
        close         = { expression = "cron(30 4 * * ? *)", steps = ["BATCH_CLOSE", "NOTIFY"] }
      }
    }
    all_projects = {
      schedules = {
        notify_unscoped = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] }
      }
    }
  }

  workflows = { for code, p in local.projects : code => {
    label        = code == "all_projects" ? "all projects" : "project ${code}"
    project      = code == "all_projects" ? null : code
    enabled      = try(p.enabled, true)
    settings     = try(p.settings, {})
    alert_emails = lookup(var.project_alert_emails, code, [])
    schedules = { for name, s in try(p.schedules, {}) : name => {
      expression      = s.expression
      steps           = s.steps
      run_type        = try(s.run_type, null)
      period          = try(s.period, null)
      table           = try(s.table, null)
      as_of           = try(s.as_of, null)
      metadata_schema = try(s.metadata_schema, null)
      metadata_db     = try(s.metadata_db, null)
      load_duplicate  = try(s.load_duplicate, null)
      enabled         = try(s.enabled, true)
    } }
  } }
  workflow_name = { for k in keys(local.workflows) : k => "${local.name_prefix}_${k}_${var.env}" }

  schedules = merge([for w, wf in local.workflows : {
    for name, s in wf.schedules : "${w}.${name}" => merge(s, {
      workflow = w
      name     = name
      enabled  = var.enable_schedules && wf.enabled && s.enabled
      input = jsonencode({ for k, v in {
        schedule        = name
        steps           = s.steps
        run_type        = s.run_type
        period          = s.period
        table           = s.table
        as_of           = s.as_of
        metadata_schema = s.metadata_schema
        metadata_db     = s.metadata_db
        load_duplicate  = s.load_duplicate
      } : k => v if v != null })
    })
  }]...)

  asl_value = { for k, v in {
    known_steps  = local.known_steps
    step_cap     = { for s, c in var.step_settings : s => c.capacity }
    step_timeout = { for s, c in var.step_settings : s => c.timeout_minutes }
    step_fail_on = { for s, c in var.step_settings : s => c.fail_on_problems ? "1,2" : "2" }
  } : k => trimsuffix(trimprefix(jsonencode(jsonencode(v)), "\""), "\"") }

  alert_topics = var.enable_failure_alerts ? merge(
    { for k, t in aws_sns_topic.workflow : k => t.arn },
    { for k, w in local.workflows : k => aws_sns_topic.ops[0].arn if w.project == null },
  ) : {}
  topics_by_name = var.enable_failure_alerts ? merge({ ops = aws_sns_topic.ops[0].arn }, { for k, t in aws_sns_topic.workflow : k => t.arn }) : {}
  subscriptions = var.enable_failure_alerts ? merge(
    var.alert_email == "" ? {} : { "ops|${var.alert_email}" = { topic = aws_sns_topic.ops[0].arn, email = var.alert_email } },
    merge([for k, t in aws_sns_topic.workflow : {
      for e in distinct(compact(concat(local.workflows[k].alert_emails, [var.alert_email]))) :
      "${k}|${e}" => { topic = t.arn, email = e }
    }]...),
  ) : {}
}
