# CMS Compliance Framework — Metadata Glue Job (Terragrunt / Terraform)

Deploys, end to end through Terragrunt, a single AWS Glue Python Shell job
that upserts a CSV file into **any** Postgres table — not hardcoded to a
fixed set of tables, and no config file to maintain. Which table, which
file, and the primary key are passed directly as job parameters at run
time (console **Run job** → Job parameters, or
`aws glue start-job-run --arguments`). DDL/tables are assumed to already
exist — this job only loads data, it never creates schema.

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
  s3-artifacts.tf        Bucket + aws_s3_bucket_object uploads (script,
                         rds_conn.py, DDL for reference, seed CSVs)
  secrets.tf             aws_secretsmanager_secret with the Postgres credentials
  glue-job.tf            IAM role, aws_glue_job, optional VPC connection/schedule,
                         SNS + CloudWatch failure alerting
  code/                  The actual application code (not Terraform)
    glue_job_metadata_load.py   Generic Glue Python Shell entry point — no
                                 per-table logic, no config file. Table name,
                                 file, and primary key are job parameters.
    rds_conn.py                 RdsClient — Secrets Manager -> pg8000 connection helper
    ddl/create_metadata_tables.sql   Reference only - assumed already applied
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

Deploying only stands up the job — it doesn't load anything. Every load is
a manual run with the parameters below.

## Running the job

One run = one file into one table. Nothing is hardcoded per table — you
tell the job which table, which file, and the primary key every time.

**Console:** open the job → **Run job** → **Job parameters** → add:

| Key | Example value |
|---|---|
| `--TABLE_NAME` | `cms_compliance.compliance_source_system` |
| `--S3_INPUT_PATH` | `s3://<artifacts-bucket>/cms-compliance-metadata/seed_data/` |
| `--S3_FILE_NAME` | `compliance_source_system.csv` |
| `--PRIMARY_KEY` | `src_cd` |
| `--MODE` | `upsert` (or `insert_only` for an append-only table — optional, default `upsert`) |

**CLI**, same thing:

```bash
aws glue start-job-run \
  --job-name cms_compliance_metadata_load_dev \
  --arguments '{
    "--TABLE_NAME": "cms_compliance.compliance_source_system",
    "--S3_INPUT_PATH": "s3://<artifacts-bucket>/cms-compliance-metadata/seed_data/",
    "--S3_FILE_NAME": "compliance_source_system.csv",
    "--PRIMARY_KEY": "src_cd"
  }'
```

A few notes on the parameters:

- `--S3_INPUT_PATH` + `--S3_FILE_NAME` are separate on purpose: the job
  reads `s3://<bucket>/<S3_INPUT_PATH prefix>/<S3_FILE_NAME>`. The job's
  default `--S3_INPUT_PATH` (set by Terraform) already points at
  `.../seed_data/`, so for a file that lives there you only need to pass
  `--S3_FILE_NAME` (and `--TABLE_NAME` / `--PRIMARY_KEY`) — override
  `--S3_INPUT_PATH` too if the file lives somewhere else in the bucket.
- `--PRIMARY_KEY` takes one or more columns, comma-separated, e.g.
  `project_cd,table_nm,src_cd,run_ty,cmplnc_vrsn` for a composite key.
- The file's own CSV header defines which columns get loaded — it must
  match the table's columns minus whichever audit columns the table owns
  (`created_dtts`, `updated_dtts`, `loaded_dtts`, `created_by`,
  `updated_by` by default). Pass `--AUDIT_COLUMNS` (comma-separated) to
  replace that default list for a table that names its audit columns
  differently — this replaces the list, it doesn't add to it.
- `--RDS_SECRET_NM`, `--RDS_DATABASE_NM`, `--REGION` are already set as job
  defaults by Terraform (from that environment's `.tfvars`) — you don't
  need to pass them unless you want to point at a different secret/db for
  one run.

### The framework's 5 metadata tables

Reference commands for the tables this repo ships seed data for
(`aws/glue/code/seed_data/*.csv`) — swap `dev` for `test`/`prod` and adjust
the bucket:

```bash
BUCKET=<artifacts-bucket>
INPUT=s3://$BUCKET/cms-compliance-metadata/seed_data/

aws glue start-job-run --job-name cms_compliance_metadata_load_dev --arguments "{
  \"--TABLE_NAME\": \"cms_compliance.compliance_source_system\",
  \"--S3_INPUT_PATH\": \"$INPUT\", \"--S3_FILE_NAME\": \"compliance_source_system.csv\",
  \"--PRIMARY_KEY\": \"src_cd\" }"

aws glue start-job-run --job-name cms_compliance_metadata_load_dev --arguments "{
  \"--TABLE_NAME\": \"cms_compliance.compliance_run_type\",
  \"--S3_INPUT_PATH\": \"$INPUT\", \"--S3_FILE_NAME\": \"compliance_run_type.csv\",
  \"--PRIMARY_KEY\": \"run_ty\" }"

aws glue start-job-run --job-name cms_compliance_metadata_load_dev --arguments "{
  \"--TABLE_NAME\": \"cms_compliance.compliance_dataset_source_xwalk\",
  \"--S3_INPUT_PATH\": \"$INPUT\", \"--S3_FILE_NAME\": \"compliance_dataset_source_xwalk.csv\",
  \"--PRIMARY_KEY\": \"project_cd,table_nm,src_cd,run_ty,cmplnc_vrsn\" }"

aws glue start-job-run --job-name cms_compliance_metadata_load_dev --arguments "{
  \"--TABLE_NAME\": \"cms_compliance.compliance_request_intake\",
  \"--S3_INPUT_PATH\": \"$INPUT\", \"--S3_FILE_NAME\": \"compliance_request_intake.csv\",
  \"--PRIMARY_KEY\": \"intake_id\" }"

aws glue start-job-run --job-name cms_compliance_metadata_load_dev --arguments "{
  \"--TABLE_NAME\": \"cms_compliance.cms_compliance_exceptions_audit\",
  \"--S3_INPUT_PATH\": \"$INPUT\", \"--S3_FILE_NAME\": \"cms_compliance_exceptions_audit.csv\",
  \"--PRIMARY_KEY\": \"event_id\", \"--MODE\": \"insert_only\" }"
```

## Loading any other table

No code change and no redeploy needed:

1. Make sure the table already exists in Postgres.
2. Drop a CSV for it under `aws/glue/code/seed_data/` whose header is the
   table's columns minus its audit columns, then `terragrunt apply` so
   Terraform uploads the new file (`etag = filemd5(...)` means only
   changed/new files get re-uploaded).
3. Run the job with that table's `--TABLE_NAME` / `--S3_FILE_NAME` /
   `--PRIMARY_KEY` (and `--MODE`/`--AUDIT_COLUMNS` if it's not a plain
   upsert or uses different audit column names).

## How updates work day to day

A later manual update (a new intake request, a corrected xwalk row, a
source flagged inactive, a new exception/audit event) is: edit the
relevant CSV under `aws/glue/code/seed_data/`, `terragrunt apply` to
re-upload it, then run the job again with that table's parameters. The
same job does inserts and updates: reference and intake tables upsert in
place; the exceptions/audit log is append-only (`--MODE insert_only`).

**Failure alerts** — set `alert_email` in the environment's `.tfvars` to
get an email via SNS whenever the Glue job reports a `FAILED` state
(CloudWatch event rule) or a failed task execution (CloudWatch metric
alarm). Set `enable_failure_alerts = false` to turn this off.

**Recurring runs** — flip `enable_schedule = true` (and set
`schedule_cron`) in the environment's `.tfvars` if you want the job to run
on a cron instead of purely on demand. A scheduled trigger runs the job
with only its Terraform-set defaults, so it only makes sense once you've
picked one fixed table/file/PK to run on that schedule (set those as
additional keys in `default_arguments` in `glue-job.tf` if you go this
route) — for the "any table, ad hoc" use case, on-demand manual runs are
the intended way to use this job.

## Destroying

```bash
cd infra/<env>/us-east-1
terragrunt destroy
```

The S3 bucket's `force_destroy` is only `true` outside prod (computed from
`environment` inside `s3-artifacts.tf`), so a prod bucket with objects in it
won't be destroyed by accident.
