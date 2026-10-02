rds_secret_name   = "REPLACE_WITH_PROD_RDS_SECRET_NAME"
rds_database_name = null
metadata_schema   = "cms_compliance"

data_bucket_names = ["REPLACE_WITH_PROD_INBOUND_BUCKET"]
quarantine_uri    = "s3://REPLACE_WITH_PROD_INBOUND_BUCKET/quarantine/"

notify_from_email     = "REPLACE_WITH_SES_VERIFIED_SENDER@example.com"
default_notify_emails = ["REPLACE_WITH_OPS_EMAIL@example.com"]
alert_emails          = ["REPLACE_WITH_ALERT_EMAIL@example.com"]
