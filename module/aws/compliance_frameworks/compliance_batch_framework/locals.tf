data "aws_caller_identity" "current" {}

locals {
  tags       = merge(var.required_common_tags, { Environment = var.environment })
  account_id = data.aws_caller_identity.current.account_id

  artifacts_uri = "s3://${aws_s3_bucket.artifacts.id}/${var.artifacts_bucket_key}"
  code_uri      = "${local.artifacts_uri}/python_code"
  temp_dir_uri  = "${local.artifacts_uri}/temp/"
  seed_data_uri = "${local.artifacts_uri}/seed_data/"

  connections = var.enable_vpc_connection ? [aws_glue_connection.vpc[0].name] : null

  wheels = fileset("${path.module}/dist/wheelhouse", "*.whl")
  python_modules = concat(
    [for w in sort(tolist(local.wheels)) : "${local.artifacts_uri}/wheelhouse/${w}"],
    var.extra_python_modules,
  )

  framework_settings = merge(
    {
      DB_SECRET_NAME        = aws_secretsmanager_secret.db.name
      DB_SSLMODE            = "require"
      METADATA_SCHEMA       = var.metadata_schema
      AWS_REGION            = var.region
      BUSINESS_TZ           = var.business_tz
      OBJECT_STORE          = "s3"
      QUARANTINE_URI        = var.quarantine_uri
      NOTIFY_BACKEND        = "ses"
      NOTIFY_FROM_EMAIL     = var.notify_from_email
      DEFAULT_NOTIFY_EMAILS = join(",", var.default_notify_emails)
      RULE_ENGINE           = var.rule_engine
    },
    var.gre_entrypoint == "" ? {} : { GRE_ENTRYPOINT = var.gre_entrypoint },
    var.framework_settings,
  )

  runner_job_name = "${var.name_prefix}_runner_${var.environment}"
  known_steps     = sort(keys(var.step_settings))

  workflows = { for code, p in var.projects : code => {
    label        = code == "all_projects" ? "all projects" : "project ${code}"
    project      = code == "all_projects" ? null : code
    enabled      = p.enabled
    settings     = p.settings
    alert_emails = p.alert_emails
    schedules    = p.schedules
  } }
  workflow_name = { for k in keys(local.workflows) : k => "${var.name_prefix}_${k}_${var.environment}" }

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
}

check "unscoped_notify" {
  assert {
    condition = anytrue([for s in try(values(var.projects["all_projects"].schedules), []) :
    s.enabled && contains(s.steps, "NOTIFY")])
    error_message = "No enabled all_projects schedule runs NOTIFY: events that belong to no project (e.g. CONFIG_VALIDATION_FAILED) will not be emailed."
  }
}
