data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_role" "workflow" {
  name = "${var.name_prefix}_workflow_${var.env}"
}

locals {
  tags            = merge(var.required_common_tags, { Environment = var.env })
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
}

check "unscoped_notify" {
  assert {
    condition = anytrue([for s in try(values(var.projects["all_projects"].schedules), []) :
    s.enabled && contains(s.steps, "NOTIFY")])
    error_message = "No enabled all_projects schedule runs NOTIFY: events that belong to no project (e.g. CONFIG_VALIDATION_FAILED) will not be emailed."
  }
}

resource "aws_cloudwatch_log_group" "workflow" {
  for_each = var.enable_workflow_logs ? local.workflows : {}

  name              = "/aws/vendedlogs/states/${local.workflow_name[each.key]}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_sfn_state_machine" "workflow" {
  for_each = local.workflows

  name     = local.workflow_name[each.key]
  role_arn = data.aws_iam_role.workflow.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/workflow.asl.json.tftpl", {
    workflow_label    = each.value.label
    environment       = var.env
    region            = local.region
    failure_topic_arn = lookup(local.alert_topics, each.key, "")
    timeout_seconds   = var.workflow_timeout_hours * 3600
    runner_job_name   = local.runner_job_name
    known_steps       = local.asl_value.known_steps
    step_capacity     = local.asl_value.step_cap
    step_timeout      = local.asl_value.step_timeout
    step_fail_on      = local.asl_value.step_fail_on
    project_settings  = trimsuffix(trimprefix(jsonencode(jsonencode({ for k, v in each.value.settings : "--FRAMEWORK_${k}" => v })), "\""), "\"")
    project_arg       = trimsuffix(trimprefix(jsonencode(jsonencode(each.value.project == null ? {} : { "--FW_PROJECT" = each.value.project })), "\""), "\"")
  })

  dynamic "logging_configuration" {
    for_each = var.enable_workflow_logs ? [1] : []
    content {
      log_destination        = "${aws_cloudwatch_log_group.workflow[each.key].arn}:*"
      include_execution_data = true
      level                  = "ERROR"
    }
  }

  tags = merge(local.tags, { Project = coalesce(each.value.project, "ALL") })

  lifecycle {
    precondition {
      condition     = alltrue([for s in values(each.value.schedules) : length(s.steps) > 0 && alltrue([for step in s.steps : contains(local.known_steps, step)])])
      error_message = "Workflow ${each.key}: every schedule needs steps, each one of ${join(", ", local.known_steps)}."
    }
  }
}

output "workflows" {
  description = "Step Functions workflow per project (and all_projects): name and ARN."
  value       = { for k, m in aws_sfn_state_machine.workflow : k => { name = m.name, arn = m.arn } }
}
