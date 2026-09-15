# CMS Compliance Framework — Metadata Glue Job (Terragrunt / Terraform)

Deploys, end to end through Terragrunt, everything needed to load and
maintain the CMS Compliance Framework's 5 supporting metadata tables in
PostgreSQL via a single AWS Glue Python Shell job:

- an S3 bucket holding the Glue script, DDL, and seed CSVs
- a Secrets Manager secret with the Postgres credentials
- the Glue job itself (IAM role, `pythonshell` job pulling its script from S3)

Three environments are wired up: **dev**, **test**, **prod**.

This is specific, purpose-built Terraform for this one deployment — not a
generic reusable module meant to be published or called from other projects.
The same `.tf` files are applied for all three environments, driven by
per-environment values passed in as Terragrunt inputs (bucket name, secret
name, DB credentials, etc.) — there's no `variables.tf`/`outputs.tf`
module-library split, no optional feature flags for use cases this
deployment doesn't have, and design choices that don't vary by environment
(Python Shell 3.9, 0.0625 DPU, `psycopg2-binary,pandas`, 30-minute timeout)
are hardcoded directly in the resources rather than exposed as inputs.

## Layout

```
aws/glue/
  app/                             The actual application code (not Terraform)
    glue_job_metadata_load.py      Glue Python Shell script (loads/upserts all 5 tables)
    ddl/create_metadata_tables.sql Postgres DDL (CREATE TABLE IF NOT EXISTS)
    seed_data/*.csv                One-time seed data, one CSV per table
  s3-artifacts/main.tf             Bucket + aws_s3_object uploads (of app/ above)
  secrets/main.tf                  aws_secretsmanager_secret with the Postgres credentials
  glue-job/main.tf                 IAM role + aws_glue_job (+ optional VPC connection / schedule)

infra/
  terragrunt.hcl                    Root config: remote state + AWS provider, shared by every component
  dev/env.hcl                       Per-env settings: region, existing state bucket/lock table name
  test/env.hcl
  prod/env.hcl
  dev/us-east-1/
    s3-artifacts/terragrunt.hcl     Applies aws/glue/s3-artifacts with this env's values
    secrets/terragrunt.hcl          Applies aws/glue/secrets with this env's values
    glue-job/terragrunt.hcl         Applies aws/glue/glue-job, depends on the two above
  test/us-east-1/...                Same 3 components, test environment
  prod/us-east-1/...                Same 3 components, prod environment
```

`infra/` holds only the per-environment **Terragrunt live configs** — each
one's `terraform { source = ... }` points at the matching `.tf` file under
`aws/glue/`. Everything is deployed exclusively through Terragrunt: there's
no `terraform apply` run by hand against `aws/glue/*` directly, only via the
`terragrunt.hcl` files under `infra/<env>/us-east-1/<component>/`.

## Prerequisites

- Terraform >= 1.5, Terragrunt >= 0.55 (any recent 0.6x is fine too)
- AWS credentials for the target account, with permission to manage S3,
  Secrets Manager, IAM, and Glue
- Your **existing** Terraform state bucket + DynamoDB lock table for each of
  dev / test / prod

## One-time setup

1. **State backend** — edit `infra/<env>/env.hcl` for each environment and
   replace the placeholders:
   ```hcl
   state_bucket = "REPLACE_WITH_YOUR_DEV_TF_STATE_BUCKET"
   lock_table   = "REPLACE_WITH_YOUR_DEV_TF_LOCK_TABLE"
   ```
   with the real bucket/table you already have for that environment. Also
   double check `aws_region` in each `env.hcl`.

2. **Postgres credentials** — the `secrets` component reads DB credentials
   from environment variables at apply time (never committed to the repo):
   ```
   export CMS_DB_HOST_DEV=your-dev-db-host.example.com
   export CMS_DB_USERNAME_DEV=your_dev_user
   export CMS_DB_PASSWORD_DEV='your-dev-password'
   ```
   (swap `_DEV` for `_TEST` / `_PROD` per environment).

3. **VPC access** — if Postgres is only reachable from inside a VPC (e.g.
   private RDS), open `glue-job/terragrunt.hcl` for that environment and set:
   ```hcl
   enable_vpc_connection = true
   subnet_id             = "subnet-xxxxxxxx"
   security_group_ids    = ["sg-xxxxxxxx"]
   availability_zone     = "us-east-1a"
   ```
   Leave `false` if Postgres is publicly reachable — no change needed.

## Deploying

Per environment, from the environment's directory (dependencies are declared
via `dependency` blocks, so `run-all` applies them in the right order:
`s3-artifacts` and `secrets` first, then `glue-job`):

```bash
cd infra/dev/us-east-1
terragrunt run-all plan
terragrunt run-all apply
```

Or one component at a time:

```bash
cd infra/dev/us-east-1/s3-artifacts && terragrunt apply
cd ../secrets                         && terragrunt apply
cd ../glue-job                        && terragrunt apply
```

Repeat under `infra/test/us-east-1` and `infra/prod/us-east-1` for the other
environments (each is fully independent — separate bucket, secret, job, and
state path).

## How updates work day to day

- **First run** seeds all 5 tables from `aws/glue/app/seed_data/*.csv`.
- **Later manual updates** (a new intake request, a corrected xwalk row, a
  source flagged inactive, a new exception/audit event): edit the relevant
  CSV under `aws/glue/app/seed_data/`, re-run `terragrunt apply` on
  `s3-artifacts` (uploads the changed file — `etag = filemd5(...)` means
  Terraform only re-uploads files that actually changed), then run the Glue
  job again (via the AWS console/CLI, or flip `enable_schedule = true` in
  `glue-job/terragrunt.hcl` for a recurring cron). The same job does inserts
  and updates: reference and intake tables upsert in place; the
  exceptions/audit log is append-only.
- **`--TABLES`** (the `tables` input) lets you target just one table for a
  targeted reload, e.g. `tables = "compliance_request_intake"`.

## Destroying

```bash
cd infra/<env>/us-east-1
terragrunt run-all destroy
```

The S3 bucket's `force_destroy` is only `true` outside prod (computed from
`environment` inside `s3-artifacts/main.tf`), so a prod bucket with objects
in it won't be destroyed by accident.
