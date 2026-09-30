locals {
  tags        = var.required_common_tags
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name
  code_dir    = "${path.module}/../code"
  code_prefix = "${var.artifacts_bucket_key}/python_code"
  code_uri    = "s3://${var.artifacts_bucket}/${local.code_prefix}"
  temp_dir    = "s3://${var.artifacts_bucket}/${var.artifacts_bucket_key}/tmp/"
  connections = length(var.glue_connection_names) > 0 ? var.glue_connection_names : null

  scripts    = toset(["glue_framework_entry.py", "glue_job_metadata_load.py", "rds_conn.py"])
  wheels     = fileset("${local.code_dir}/wheels", "*.whl")
  wheel_uris = [for w in sort(tolist(local.wheels)) : "s3://${var.artifacts_bucket}/${var.artifacts_bucket_key}/wheels/${w}"]

  framework_settings = merge(
    {
      DB_SECRET_NAME        = var.rds_secret_name
      DB_SSLMODE            = "require"
      METADATA_SCHEMA       = var.metadata_schema
      AWS_REGION            = local.region
      BUSINESS_TZ           = var.business_tz
      OBJECT_STORE          = "s3"
      QUARANTINE_URI        = var.quarantine_uri
      NOTIFY_BACKEND        = "ses"
      NOTIFY_FROM_EMAIL     = var.notify_from_email
      DEFAULT_NOTIFY_EMAILS = join(",", var.default_notify_emails)
      RULE_ENGINE           = var.rule_engine
    },
    var.gre_entrypoint == "" ? {} : { GRE_ENTRYPOINT = var.gre_entrypoint },
    var.rds_database_name == null ? {} : { DB_NAME = var.rds_database_name },
    var.framework_settings,
  )

  common_arguments = {
    "--additional-python-modules"        = join(",", var.pypi_packages)
    "--env"                              = var.env
    "--region"                           = local.region
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--job-bookmark-option"              = "job-bookmark-disable"
    "--TempDir"                          = local.temp_dir
  }

  artifacts_arn = "arn:aws:s3:::${var.artifacts_bucket}"
  secret_arn    = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:${var.rds_secret_name}-*"
}
