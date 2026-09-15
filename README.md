# CMS Compliance Framework — Metadata Glue Job (Terragrunt / Terraform)

Deploys, end to end through Terragrunt, everything needed to load and
maintain the CMS Compliance Framework's 5 supporting metadata tables in
PostgreSQL via a single AWS Glue Python Shell job.

This follows the same conventions as the team's other Glue job Terraform
(`aae-aws-artf/module/aws/part c odr/glue-jobs/`):

- one `.tf` file per concern, living together in one folder (no
  `variables.tf`/`outputs.tf` module-library split beyond the single shared
  `variables.tf` here) — `s3-artifacts.tf`, `secrets.tf`, `glue-job.tf`
- the script + its `rds_conn.py` connection helper live in a `code/`
  subfolder next to the `.tf` files, uploaded to S3 with `aws_s3_bucket_object`
- Postgres connectivity uses the same `RdsClient` pattern (Secrets Manager →
  `pg8000`) as `glue-jobs/code/rds_conn.py`, just pointed at Postgres instead
  of that job's Teradata/RDS pairing
- Glue job failure alerting uses the same SNS + CloudWatch event
  rule/target/policy + `ExecutionsFailed`-style metric alarm pattern as
  `glue_file_ingestion.tf`
- **environment values live in `.tfvars` files co-located with the `.tf`
  files** (`aws/glue/dev.tfvars`, `test.tfvars`, `prod.tfvars`) rather than
  in separate per-environment directories

This is specific, purpose-built Terraform for this one deployment — not a
generic reusable module. The same `.tf` files are applied for dev / test /
prod, driven by whichever `.tfvars` file Terragrunt passes in for that
environment. Design choices that don't vary by environment (Python Shell
3.9, 0.0625 DPU, `pg8000,pandas`, 30-minute timeout, `max_concurrent_runs`)
are hardcoded directly in `glue-job.tf` rather than exposed as variables.

## Layout

```
aws/glue/
  variables.tf          All input variables for this deployment
  dev.tfvars             Environment-specific values — dev
  test.tfvars             "                              " — test
  prod.tfvars              "                              " — prod
  s3-artifacts.tf        Bucket + aws_s3_bucket_object uploads (script, rds_conn.py, DDL, seed CSVs)
  secrets.tf             aws_secretsmanager_secret with the Postgres credentials
  glue-job.tf            IAM role, aws_glue_job, optional VPC connection/schedule,
                         SNS + CloudWatch failure alerting
  code/                  The actual application code (not Terraform)
    glue_job_metadata_load.py   Glue Python Shell entry point (loads/upserts all 5 tables)
    rds_conn.py                 RdsClient — Secrets Manager -> pg8000 connection helper
    ddl/create_metadata_tables.sql
    seed_data/*.csv        One-time seed data, one CSV per table

infra/
  terragrunt.hcl                 Root config: remote state + AWS provider, shared by every environment
  dev/env.hcl                    Per-env settings: region, existing state bucket/lock table name
  test/env.hcl
  prod/env.hcl
  dev/us-east-1/terragrunt.hcl   Applies aws/glue with dev.tfvars (via extra_arguments -var-file)
  test/us-east-1/terragrunt.hcl  Applies aws/glue with test.tfvars
  prod/us-east-1/terragrunt.hcl  Applies aws/glue with prod.tfvars
```

Everything is deployed exclusively through Terragrunt: there's no
`terraform apply` run by hand against `aws/glue` directly, only via the
`terragrunt.hcl` files under `infra/<env>/us-east-1/`. Each one points
`source` at `../../../aws/glue` and adds an `extra_arguments` block that
passes `-var-file=<...>/aws/glue/<env>.tfvars` on every `plan`/`apply`, so
the co-located tfvars file is what actually varies per environment.

## Prerequisites

- Terraform >= 1.5, Terragrunt >= 0.55 (any recent 0.6x is fine too)
- AWS credentials for the target account, with permission to manage S3,
  Secrets Manager, IAM, Glue, SNS, and CloudWatch
- Your **existing** Terraform state bucket + DynamoDB lock table for each of
  dev / test / prod

## One-time setup

1. **State backend** — edit `infra/<env>/env.hcl` for each environment and
   replace the placeholders:
   ```hcl
   state_bucket = "REPLACE_WITH_YOUR_DEV_TF_STATE_BUCKET"
   lock_table   = "REPLACE_WITH_YOUR_DEV_TF_LOCK_TABLE"
   ```
   with the real bucket/table you already have for that environment.

2. **Environment values** — edit `aws/glue/<env>.tfvars` and replace every
   `REPLACE_WITH_...` placeholder: account id, artifacts bucket name, DB
   host/name, Glue IAM role name, alert email, etc.

3. **Postgres credentials** — `db_username` / `db_password` are declared as
   `sensitive` variables and are **not** set in any `.tfvars` file. Pass them
   at apply time instead:
   ```bash
   export TF_VAR_db_username=your_db_user
   export TF_VAR_db_password='your-db-password'
   ```

4. **VPC access** — if Postgres is only reachable from inside a VPC, set in
   the environment's `.tfvars`:
   ```hcl
   enable_vpc_connection = true
   subnet_id             = "subnet-xxxxxxxx"
   security_group_ids    = ["sg-xxxxxxxx"]
   availability_zone     = "us-east-1a"
   ```
   Leave `false` if Postgres is publicly reachable.

5. **Glue IAM role** — `glue_role_name` in each `.tfvars` names the role
   Terraform creates for the job (`aws_iam_role.glue` in `glue-job.tf`).
   Rename it to match your account's naming convention if you have one
   (e.g. the `ENTERPRISE/<ROLE>` pattern used by the team's other jobs).

## Deploying

```bash
cd infra/dev/us-east-1
terragrunt plan
terragrunt apply
```

Repeat under `infra/test/us-east-1` and `infra/prod/us-east-1` for the other
environments (each is fully independent — separate bucket, secret, job, and
state path, driven by that folder's own `.tfvars`).

## How updates work day to day

- **First run** seeds all 5 tables from `aws/glue/code/seed_data/*.csv`.
- **Later manual updates** (a new intake request, a corrected xwalk row, a
  source flagged inactive, a new exception/audit event): edit the relevant
  CSV under `aws/glue/code/seed_data/`, re-run `terragrunt apply` (uploads
  the changed file — `etag = filemd5(...)` means Terraform only re-uploads
  files that actually changed), then run the Glue job again (via the AWS
  console/CLI, or flip `enable_schedule = true` in the environment's
  `.tfvars` for a recurring cron). The same job does inserts and updates:
  reference and intake tables upsert in place; the exceptions/audit log is
  append-only.
- **`tables`** (the `--TABLES` job argument) lets you target just one table
  for a reload, e.g. set `tables = "compliance_request_intake"` in that
  environment's `.tfvars`.
- **Failure alerts** — set `alert_email` in the environment's `.tfvars` to
  get an email via SNS whenever the Glue job reports a `FAILED` state
  (CloudWatch event rule) or a failed task execution (CloudWatch metric
  alarm). Set `enable_failure_alerts = false` to turn this off.

## Destroying

```bash
cd infra/<env>/us-east-1
terragrunt destroy
```

The S3 bucket's `force_destroy` is only `true` outside prod (computed from
`environment` inside `s3-artifacts.tf`), so a prod bucket with objects in it
won't be destroyed by accident.
