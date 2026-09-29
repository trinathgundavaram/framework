locals {
  environment = "prod"
  aws_region  = "us-east-1"

  state_bucket = "REPLACE_WITH_YOUR_PROD_TF_STATE_BUCKET"
  lock_table   = "REPLACE_WITH_YOUR_PROD_TF_LOCK_TABLE"
}
