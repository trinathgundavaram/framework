environment = "test"
region      = "us-east-1"
account_id  = "REPLACE_WITH_TEST_ACCOUNT_ID"

artifacts_bucket     = "REPLACE_WITH_TEST_ARTIFACTS_BUCKET_NAME"
artifacts_bucket_key = "cms-compliance-metadata"

rds_secret_name   = "cms-compliance/test/postgres"
rds_database_name = "REPLACE_WITH_TEST_DB_NAME"
db_host           = "REPLACE_WITH_TEST_DB_HOST"
db_port           = 5432
# db_username / db_password: pass via TF_VAR_db_username / TF_VAR_db_password
# at apply time - never commit real credentials in this file.

job_name       = "cms_compliance_metadata_load_test"
glue_role_name = "cms_compliance_metadata_load_test-role"

run_ddl = true
tables  = ""

enable_vpc_connection = false
enable_schedule        = false

enable_failure_alerts = true
alert_email           = "REPLACE_WITH_ALERT_EMAIL@example.com"

required_common_tags = {
  Environment  = "test"
  AppName      = "CMS Compliance Framework"
  BusinessEntity = "Compliance"
}
