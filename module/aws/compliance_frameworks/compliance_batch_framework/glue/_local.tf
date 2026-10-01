locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  name_prefix                = "compliance_batch_framework"
  metadata_schema            = "cms_compliance"
  business_tz                = "America/Chicago"
  rule_engine                = "gre"
  gre_entrypoint             = ""
  extra_framework_settings   = {}
  runner_max_concurrent_runs = 20

  runner_job_name        = "${local.name_prefix}_runner_${var.env}"
  metadata_load_job_name = "${local.name_prefix}_metadata_load_${var.env}"

  artifacts_bucket = "silverton-maa-global-artifactory-${var.env}"
  artifacts_arn    = "arn:aws:s3:::${local.artifacts_bucket}"
  s3_prefix        = "compliance_batch_framework"

  glue_script_prefix  = "${local.s3_prefix}/glue"
  wheel_prefix        = "${local.s3_prefix}/whl"
  config_data_prefix  = "${local.s3_prefix}/config_data"
  tmp_prefix          = "${local.s3_prefix}/tmp"
  runner_script_key   = "${local.glue_script_prefix}/glue_framework_entry.py"
  metadata_script_key = "${local.glue_script_prefix}/glue_job_metadata_load.py"
  rds_conn_script_key = "${local.glue_script_prefix}/rds_conn.py"
  glue_scripts        = toset(["glue_framework_entry.py", "glue_job_metadata_load.py", "rds_conn.py"])
  code_dir            = "${path.module}/../code"
  wheels              = fileset("${local.code_dir}/wheels", "*.whl")
  wheel_uris          = [for w in sort(tolist(local.wheels)) : "s3://${local.artifacts_bucket}/${local.wheel_prefix}/${w}"]
  pypi_packages       = "psycopg[binary]==3.2.13,tzdata==2026.4,pg8000==1.31.5"
  connections         = ["REPLACE_WITH_POSTGRES_GLUE_CONNECTION_NAME"]
  secret_arn          = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:${var.rds_secret_name}-*"

  tags = merge(var.required_common_tags, {
    Environment = var.env
    ManagedBy   = "Terraform"
  })

  framework_settings = merge(
    {
      DB_SECRET_NAME        = var.rds_secret_name
      DB_SSLMODE            = "require"
      METADATA_SCHEMA       = local.metadata_schema
      AWS_REGION            = local.region
      BUSINESS_TZ           = local.business_tz
      OBJECT_STORE          = "s3"
      QUARANTINE_URI        = var.quarantine_uri
      NOTIFY_BACKEND        = "ses"
      NOTIFY_FROM_EMAIL     = var.notify_from_email
      DEFAULT_NOTIFY_EMAILS = join(",", var.default_notify_emails)
      RULE_ENGINE           = local.rule_engine
    },
    local.gre_entrypoint == "" ? {} : { GRE_ENTRYPOINT = local.gre_entrypoint },
    var.rds_database_name == null ? {} : { DB_NAME = var.rds_database_name },
    local.extra_framework_settings,
  )

  common_arguments = {
    "--additional-python-modules"        = local.pypi_packages
    "--env"                              = var.env
    "--region"                           = local.region
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--job-bookmark-option"              = "job-bookmark-disable"
    "--TempDir"                          = "s3://${local.artifacts_bucket}/${local.tmp_prefix}/"
  }

}
