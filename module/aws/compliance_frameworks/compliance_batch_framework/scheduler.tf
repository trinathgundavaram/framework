resource "aws_scheduler_schedule_group" "workflow" {
  for_each = local.workflows

  name = replace(local.workflow_name[each.key], "_", "-")
  tags = merge(local.tags, { Project = coalesce(each.value.project, "ALL") })
}

resource "aws_scheduler_schedule" "workflow" {
  for_each = local.schedules

  name                         = each.value.name
  group_name                   = aws_scheduler_schedule_group.workflow[each.value.workflow].name
  description                  = "${local.workflows[each.value.workflow].label}: ${join(" -> ", each.value.steps)}"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone
  state                        = each.value.enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.workflow[each.value.workflow].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = each.value.input

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 5
    }

    dead_letter_config {
      arn = aws_sqs_queue.schedule_dlq.arn
    }
  }

  lifecycle {
    precondition {
      condition     = can(regex("^(cron|rate|at)\\(.+\\)$", each.value.expression)) && can(regex("^[A-Za-z0-9_.-]{1,64}$", each.value.name))
      error_message = "Schedule ${each.key}: expression must be cron(...), rate(...) or at(...); the name letters, digits, _ . -"
    }
  }
}

resource "aws_sqs_queue" "schedule_dlq" {
  name                      = "${var.name_prefix}_schedule_dlq_${var.environment}"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_iam_role" "scheduler" {
  name = "${var.name_prefix}_scheduler_${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.name_prefix}_scheduler_${var.environment}"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartWorkflows"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = [for m in aws_sfn_state_machine.workflow : m.arn]
      },
      {
        Sid      = "DeadLetters"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.schedule_dlq.arn]
      }
    ]
  })
}
