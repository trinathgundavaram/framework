locals {
  asl_value = { for k, v in {
    known_steps  = local.known_steps
    step_cap     = { for s, c in var.step_settings : s => c.capacity }
    step_timeout = { for s, c in var.step_settings : s => c.timeout_minutes }
    step_fail_on = { for s, c in var.step_settings : s => c.fail_on_problems ? "1,2" : "2" }
  } : k => trimsuffix(trimprefix(jsonencode(jsonencode(v)), "\""), "\"") }
}

resource "aws_cloudwatch_log_group" "workflow" {
  for_each = local.workflows

  name              = "/aws/vendedlogs/states/${local.workflow_name[each.key]}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_sfn_state_machine" "workflow" {
  for_each = local.workflows

  name     = local.workflow_name[each.key]
  role_arn = aws_iam_role.workflow.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/stepfunctions/workflow.asl.json.tftpl", {
    workflow_label    = each.value.label
    environment       = var.environment
    region            = var.region
    failure_topic_arn = lookup(local.alert_topics, each.key, "")
    timeout_seconds   = var.workflow_timeout_hours * 3600
    runner_job_name   = aws_glue_job.runner.name
    known_steps       = local.asl_value.known_steps
    step_capacity     = local.asl_value.step_cap
    step_timeout      = local.asl_value.step_timeout
    step_fail_on      = local.asl_value.step_fail_on
    project_settings  = trimsuffix(trimprefix(jsonencode(jsonencode({ for k, v in each.value.settings : "--FRAMEWORK_${k}" => v })), "\""), "\"")
    project_arg       = trimsuffix(trimprefix(jsonencode(jsonencode(each.value.project == null ? {} : { "--FW_PROJECT" = each.value.project })), "\""), "\"")
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.workflow[each.key].arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tags = merge(local.tags, { Project = coalesce(each.value.project, "ALL") })

  lifecycle {
    precondition {
      condition     = alltrue([for s in values(each.value.schedules) : length(s.steps) > 0 && alltrue([for step in s.steps : contains(local.known_steps, step)])])
      error_message = "Workflow ${each.key}: every schedule needs steps, each one of ${join(", ", local.known_steps)}."
    }
  }
}

resource "aws_iam_role" "workflow" {
  name = "${var.name_prefix}_workflow_${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "workflow" {
  name = "${var.name_prefix}_workflow_${var.environment}"
  role = aws_iam_role.workflow.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunTheRunner"
        Effect   = "Allow"
        Action   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
        Resource = ["arn:aws:glue:${var.region}:${local.account_id}:job/${aws_glue_job.runner.name}"]
      },
      {
        Sid      = "FailureAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = length(local.all_topic_arns) > 0 ? local.all_topic_arns : ["arn:aws:sns:${var.region}:${local.account_id}:none"]
      },
      {
        Sid    = "ExecutionLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
        "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
        Resource = ["*"]
      }
    ]
  })
}
