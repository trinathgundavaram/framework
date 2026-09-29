resource "aws_sns_topic" "ops" {
  count = var.enable_failure_alerts ? 1 : 0
  name  = "${var.name_prefix}_ops_${var.environment}"
  tags  = local.tags
}

resource "aws_sns_topic" "workflow" {
  for_each = var.enable_failure_alerts ? { for k, w in local.workflows : k => w if w.project != null } : {}

  name = "${local.workflow_name[each.key]}_failures"
  tags = merge(local.tags, { Project = each.value.project })
}

locals {
  alert_topics = var.enable_failure_alerts ? merge(
    { for k, t in aws_sns_topic.workflow : k => t.arn },
    { for k, w in local.workflows : k => aws_sns_topic.ops[0].arn if w.project == null },
  ) : {}
  topics_by_name = var.enable_failure_alerts ? merge({ ops = aws_sns_topic.ops[0].arn }, { for k, t in aws_sns_topic.workflow : k => t.arn }) : {}
  all_topic_arns = values(local.topics_by_name)
  subscriptions = var.enable_failure_alerts ? merge(
    var.alert_email == "" ? {} : { "ops|${var.alert_email}" = { topic = aws_sns_topic.ops[0].arn, email = var.alert_email } },
    merge([for k, t in aws_sns_topic.workflow : {
      for e in distinct(compact(concat(local.workflows[k].alert_emails, [var.alert_email]))) :
      "${k}|${e}" => { topic = t.arn, email = e }
    }]...),
  ) : {}
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = local.subscriptions
  topic_arn = each.value.topic
  protocol  = "email"
  endpoint  = each.value.email
}

resource "aws_sns_topic_policy" "alerts" {
  for_each = local.topics_by_name
  arn      = each.value

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["events.amazonaws.com", "cloudwatch.amazonaws.com"] }
      Action    = "sns:Publish"
      Resource  = each.value
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_cloudwatch_event_rule" "workflow_stopped" {
  for_each = local.alert_topics

  name        = "${local.workflow_name[each.key]}_stopped"
  description = "${local.workflows[each.key].label}: workflow execution timed out or was aborted"

  event_pattern = jsonencode({
    source        = ["aws.states"]
    "detail-type" = ["Step Functions Execution Status Change"]
    detail = {
      stateMachineArn = [aws_sfn_state_machine.workflow[each.key].arn]
      status          = ["TIMED_OUT", "ABORTED"]
    }
  })

  tags = local.tags
}

resource "aws_cloudwatch_event_target" "workflow_stopped" {
  for_each = local.alert_topics

  rule      = aws_cloudwatch_event_rule.workflow_stopped[each.key].name
  target_id = "sns"
  arn       = each.value
}

resource "aws_cloudwatch_metric_alarm" "schedule_dlq" {
  count = var.enable_failure_alerts ? 1 : 0

  alarm_name          = "${var.name_prefix}_schedule_dlq_${var.environment}"
  alarm_description   = "A schedule could not start its workflow after retries; the event is in ${aws_sqs_queue.schedule_dlq.name}"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.schedule_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops[0].arn]
  tags                = local.tags
}
