name_prefix = "compliance_batch_framework"

artifacts_bucket     = "silverton-maa-global-artifactory-dev"
artifacts_bucket_key = "compliance_batch_framework"

rds_secret_name   = "REPLACE_WITH_DEV_RDS_SECRET_NAME"
rds_database_name = null
metadata_schema   = "cms_compliance"

glue_version          = null
python_version        = "3.9"
glue_security_conf    = null
glue_connection_names = ["REPLACE_WITH_DEV_GLUE_RDS_CONNECTION"]
pypi_packages         = ["psycopg[binary]==3.2.13", "tzdata==2026.4", "pg8000==1.31.5"]

data_bucket_names = ["REPLACE_WITH_DEV_INBOUND_BUCKET"]
quarantine_uri    = "s3://REPLACE_WITH_DEV_INBOUND_BUCKET/quarantine/"
kms_key_arns      = []

business_tz           = "America/Chicago"
notify_from_email     = "REPLACE_WITH_SES_VERIFIED_SENDER@example.com"
default_notify_emails = ["REPLACE_WITH_OPS_EMAIL@example.com"]
rule_engine           = "gre"
gre_entrypoint        = ""
framework_settings    = {}

alert_emails = ["REPLACE_WITH_ALERT_EMAIL@example.com"]

permissions_boundary = null

required_common_tags = {
  AppName   = "Compliance"
  ManagedBy = "Terraform"
}
