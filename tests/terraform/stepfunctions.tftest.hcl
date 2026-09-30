mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_region" { defaults = { name = "us-east-1" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/r" } }
  mock_resource "aws_sfn_state_machine" { defaults = { arn = "arn:aws:states:us-east-1:111122223333:stateMachine:m" } }
  mock_resource "aws_sns_topic" { defaults = { arn = "arn:aws:sns:us-east-1:111122223333:t" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:us-east-1:111122223333:q" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:us-east-1:111122223333:log-group:g" } }
}

variables {
  env = "dev"
}

run "dev_plan" {
  command = apply

  assert {
    condition     = length(aws_sfn_state_machine.workflow) == 3 && length(aws_cloudwatch_event_rule.schedule) == 7 && length(aws_cloudwatch_metric_alarm.schedule_failed) == 7
    error_message = "expected 3 workflows (ODR, UNIVERSE, all_projects), 7 schedule rules and an alarm per rule"
  }
  assert {
    condition     = jsondecode(aws_cloudwatch_event_target.schedule["ODR.daily_batches"].input) == { schedule = "daily_batches", steps = ["BATCH_CREATION"], run_type = "DAILY", period = "PREV_DAY" }
    error_message = "ODR daily input: ${aws_cloudwatch_event_target.schedule["ODR.daily_batches"].input}"
  }
  assert {
    condition     = aws_cloudwatch_event_rule.schedule["UNIVERSE.close"].name == "compliance_batch_framework_UNIVERSE_dev_close" && aws_cloudwatch_event_rule.schedule["UNIVERSE.close"].state == "ENABLED"
    error_message = "schedule rule name / state"
  }
  assert {
    condition     = length(aws_sns_topic.workflow) == 2 && length(aws_cloudwatch_event_rule.workflow_stopped) == 3
    error_message = "one alert topic per project; a stopped-execution rule per workflow"
  }
  assert {
    condition     = jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).QueryLanguage == "JSONata" && length(aws_cloudwatch_log_group.workflow) == 0 && length(aws_sfn_state_machine.workflow["ODR"].logging_configuration) == 0
    error_message = "definition must be valid JSON; CloudWatch logging is off by default"
  }
  assert {
    condition     = jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).States.RunSteps.ItemProcessor.States.RunStep.Arguments.JobName == "compliance_batch_framework_runner_dev"
    error_message = "steps run the runner job"
  }
  assert {
    condition     = strcontains(jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).States.RunSteps.ItemProcessor.States.RunStep.Arguments.Arguments, "\"--FW_PROJECT\":\"ODR\"")
    error_message = "ODR steps must pass --FW_PROJECT ODR"
  }
  assert {
    condition     = !strcontains(aws_sfn_state_machine.workflow["all_projects"].definition, "FW_PROJECT")
    error_message = "all_projects steps run without --project"
  }
  assert {
    condition     = jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).States.RunSteps.MaxConcurrency == 1
    error_message = "steps must run one at a time"
  }
}

run "project_disabled_and_settings" {
  command = apply
  variables {
    projects = {
      ODR = {
        enabled   = false
        settings  = { FILE_RULES_MODE = "ANNOTATE", DEFAULT_NOTIFY_EMAILS = "o'brien@example.com" }
        schedules = { load = { expression = "rate(15 minutes)", steps = ["FILE_LOAD"] } }
      }
      all_projects = { schedules = { n = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] } } }
    }
  }
  assert {
    condition     = aws_cloudwatch_event_rule.schedule["ODR.load"].state == "DISABLED"
    error_message = "a disabled project's schedules are DISABLED"
  }
  assert {
    condition     = strcontains(jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).States.RunSteps.ItemProcessor.States.RunStep.Arguments.Arguments, "\"--FRAMEWORK_DEFAULT_NOTIFY_EMAILS\":\"o'brien@example.com\"")
    error_message = "project settings (even with quotes) reach the Glue arguments"
  }
}

run "every_name_starts_with_compliance" {
  command = apply
  assert {
    condition = alltrue(concat(
      [startswith(aws_iam_role.workflow.name, "compliance"), startswith(aws_iam_role.events.name, "compliance")],
      [for m in aws_sfn_state_machine.workflow : startswith(m.name, "compliance")],
      [for r in aws_cloudwatch_event_rule.schedule : startswith(r.name, "compliance")],
      [for r in aws_cloudwatch_event_rule.workflow_stopped : startswith(r.name, "compliance")],
      [for t in aws_sns_topic.workflow : startswith(t.name, "compliance")],
      [for a in aws_cloudwatch_metric_alarm.schedule_failed : startswith(a.alarm_name, "compliance")],
    ))
    error_message = "the deployer role only manages compliance* resources"
  }
}

run "workflow_logs_when_enabled" {
  command = apply
  variables {
    enable_workflow_logs = true
  }
  assert {
    condition     = length(aws_cloudwatch_log_group.workflow) == 3 && aws_sfn_state_machine.workflow["ODR"].logging_configuration[0].level == "ERROR"
    error_message = "enable_workflow_logs adds a log group per workflow"
  }
}

run "unknown_step_is_rejected" {
  command = plan
  variables {
    projects = {
      ODR          = { schedules = { bad = { expression = "cron(0 6 * * ? *)", steps = ["FILE_LOAD", "NOPE"] } } }
      all_projects = { schedules = { n = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] } } }
    }
  }
  expect_failures = [aws_sfn_state_machine.workflow]
}

run "missing_unscoped_notify_warns" {
  command = plan
  variables {
    projects = { ODR = { schedules = { load = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD"] } } } }
  }
  expect_failures = [check.unscoped_notify]
}
