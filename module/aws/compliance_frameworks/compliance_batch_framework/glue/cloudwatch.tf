resource "aws_sns_topic" "glue_alerts" {
  name = "${local.name_prefix}_glue_alerts_${var.env}"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "glue_alerts" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.glue_alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_sns_topic_policy" "glue_alerts" {
  arn = aws_sns_topic.glue_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.glue_alerts.arn
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_cloudwatch_event_rule" "glue_failed" {
  name        = "${local.name_prefix}_glue_failed_${var.env}"
  description = "compliance batch framework: a Glue job run failed, timed out or errored"

  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = [aws_glue_job.runner.name, aws_glue_job.metadata_load.name]
      state   = ["FAILED", "TIMEOUT", "ERROR"]
    }
  })

  tags = local.tags
}

resource "aws_cloudwatch_event_target" "glue_failed" {
  rule      = aws_cloudwatch_event_rule.glue_failed.name
  target_id = "sns"
  arn       = aws_sns_topic.glue_alerts.arn
}
