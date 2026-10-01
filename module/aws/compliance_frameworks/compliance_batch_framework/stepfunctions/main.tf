check "unscoped_notify" {
  assert {
    condition = anytrue([for s in try(values(local.workflows["all_projects"].schedules), []) :
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
  role_arn = aws_iam_role.workflow.arn
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
