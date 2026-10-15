resource "aws_cloudwatch_event_rule" "schedule" {
  for_each = local.schedules

  name                = "${local.workflow_name[each.value.workflow]}_${each.value.name}"
  description         = "${local.workflows[each.value.workflow].label}: ${join(" -> ", each.value.steps)} (UTC)"
  schedule_expression = each.value.expression
  state               = each.value.enabled ? "ENABLED" : "DISABLED"
  tags                = merge(local.tags, { Project = coalesce(local.workflows[each.value.workflow].project, "ALL") })

  lifecycle {
    precondition {
      condition     = can(regex("^(cron|rate)\\(.+\\)$", each.value.expression)) && can(regex("^[A-Za-z0-9_.-]{1,64}$", "${local.workflow_name[each.value.workflow]}_${each.value.name}"))
      error_message = "Schedule ${each.key}: expression must be cron(...) or rate(...) in UTC; the rule name (<prefix>_<project>_<env>_<schedule>) at most 64 letters, digits, _ . -"
    }
  }
}

resource "aws_cloudwatch_event_target" "schedule" {
  for_each = local.schedules

  rule      = aws_cloudwatch_event_rule.schedule[each.key].name
  target_id = "workflow"
  arn       = aws_sfn_state_machine.workflow[each.value.workflow].arn
  role_arn  = aws_iam_role.events.arn
  input     = each.value.input

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 5
  }
}
