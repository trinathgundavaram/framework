locals {
  tags            = var.required_common_tags
  account_id      = data.aws_caller_identity.current.account_id
  region          = data.aws_region.current.name
  runner_job_name = "${var.name_prefix}_runner_${var.env}"
  known_steps     = sort(keys(var.step_settings))

  workflows = { for code, p in var.projects : code => {
    label        = code == "all_projects" ? "all projects" : "project ${code}"
    project      = code == "all_projects" ? null : code
    enabled      = p.enabled
    settings     = p.settings
    alert_emails = p.alert_emails
    schedules    = p.schedules
  } }
  workflow_name = { for k in keys(local.workflows) : k => "${var.name_prefix}_${k}_${var.env}" }

  schedules = merge([for w, wf in local.workflows : {
    for name, s in wf.schedules : "${w}.${name}" => merge(s, {
      workflow = w
      name     = name
      enabled  = var.enable_schedules && wf.enabled && s.enabled
      input = jsonencode({ for k, v in {
        schedule = name
        steps    = s.steps
        run_type = s.run_type
        period   = s.period
        table    = s.table
        as_of    = s.as_of
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
