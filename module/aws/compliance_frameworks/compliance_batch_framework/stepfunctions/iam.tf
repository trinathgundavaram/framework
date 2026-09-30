resource "aws_iam_role" "workflow" {
  name                 = "${var.name_prefix}_workflow_${var.env}"
  permissions_boundary = var.permissions_boundary

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
  name = "${var.name_prefix}_workflow_${var.env}"
  role = aws_iam_role.workflow.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunTheRunner"
        Effect   = "Allow"
        Action   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
        Resource = ["arn:aws:glue:${local.region}:${local.account_id}:job/${var.name_prefix}_runner_${var.env}"]
      },
      {
        Sid      = "FailureAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = ["arn:aws:sns:${local.region}:${local.account_id}:${var.name_prefix}_*_${var.env}*"]
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

resource "aws_iam_role" "events" {
  name                 = "${var.name_prefix}_events_${var.env}"
  permissions_boundary = var.permissions_boundary

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "events" {
  name = "${var.name_prefix}_events_${var.env}"
  role = aws_iam_role.events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartWorkflows"
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = ["arn:aws:states:${local.region}:${local.account_id}:stateMachine:${var.name_prefix}_*_${var.env}"]
    }]
  })
}
