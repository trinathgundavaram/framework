mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_resource "aws_sfn_state_machine" { defaults = { arn = "arn:aws:states:us-east-1:111122223333:stateMachine:m" } }
  mock_resource "aws_sns_topic" { defaults = { arn = "arn:aws:sns:us-east-1:111122223333:t" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:us-east-1:111122223333:q" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/r" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::b" } }
  mock_resource "aws_secretsmanager_secret" { defaults = { arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:s" } }
}

variables {
  db_username = "u"
  db_password = "p"
}

run "dev_plan" {
  command = apply

  assert {
    condition     = length(aws_sfn_state_machine.workflow) == 3 && length(aws_scheduler_schedule.workflow) == 7
    error_message = "expected 3 workflows (ODR, UNIVERSE, all_projects) and 7 schedules"
  }
  assert {
    condition     = aws_glue_job.runner.name == "compliance_batch_framework_runner_dev" && aws_glue_job.runner.execution_property[0].max_concurrent_runs == 20
    error_message = "runner job"
  }
  assert {
    condition     = aws_glue_job.metadata_load.default_arguments["--additional-python-modules"] == aws_glue_job.runner.default_arguments["--additional-python-modules"] && strcontains(aws_glue_job.metadata_load.default_arguments["--additional-python-modules"], "/wheelhouse/pg8000-")
    error_message = "both jobs install from the wheelhouse (no PyPI)"
  }
  assert {
    condition     = strcontains(aws_s3_bucket_policy.artifacts.policy, "aws:SecureTransport") && aws_glue_job.runner.default_arguments["--FRAMEWORK_DB_SSLMODE"] == "require"
    error_message = "TLS only for the artifacts bucket and the database"
  }
  assert {
    condition     = jsondecode(aws_scheduler_schedule.workflow["ODR.daily_batches"].target[0].input) == { schedule = "daily_batches", steps = ["BATCH_CREATION"], run_type = "DAILY", period = "PREV_DAY" }
    error_message = "ODR daily input: ${aws_scheduler_schedule.workflow["ODR.daily_batches"].target[0].input}"
  }
  assert {
    condition     = aws_scheduler_schedule.workflow["UNIVERSE.close"].schedule_expression_timezone == "America/Chicago" && aws_scheduler_schedule.workflow["UNIVERSE.close"].state == "ENABLED"
    error_message = "schedule timezone / state"
  }
  assert {
    condition     = length(aws_sns_topic.workflow) == 2 && length(aws_cloudwatch_event_rule.workflow_stopped) == 3
    error_message = "one alert topic per project; a stopped-execution rule per workflow"
  }
  assert {
    condition     = jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).QueryLanguage == "JSONata"
    error_message = "definition must be valid JSON"
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
    condition     = aws_scheduler_schedule.workflow["ODR.load"].state == "DISABLED"
    error_message = "a disabled project's schedules are DISABLED"
  }
  assert {
    condition     = strcontains(jsondecode(aws_sfn_state_machine.workflow["ODR"].definition).States.RunSteps.ItemProcessor.States.RunStep.Arguments.Arguments, "\"--FRAMEWORK_DEFAULT_NOTIFY_EMAILS\":\"o'brien@example.com\"")
    error_message = "project settings (even with quotes) reach the Glue arguments"
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
