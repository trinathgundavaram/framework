locals {
  environment = "test"
  aws_region  = "us-east-1" # change if your test account/region differs

  # --- Fill these in with your EXISTING Terragrunt/Terraform state backend ---
  state_bucket = "REPLACE_WITH_YOUR_TEST_TF_STATE_BUCKET"
  lock_table   = "REPLACE_WITH_YOUR_TEST_TF_LOCK_TABLE"
}
