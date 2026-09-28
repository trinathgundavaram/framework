# infra/ — Terragrunt live configuration

Per-environment Terragrunt configs for the metadata-load Glue job in `aws/glue/`. The job, its
parameters and the seed data are described in the repository [README](../README.md); this file
only covers the Terragrunt side.

```
infra/
  terragrunt.hcl                  Root config: remote state + AWS provider, shared by every environment
  dev|test|prod/env.hcl           Per-env settings: region, existing state bucket / lock table
  dev|test|prod/us-east-1/terragrunt.hcl
                                  Applies aws/glue with that environment's aws/glue/<env>.tfvars
```

## One-time setup

1. Edit `infra/<env>/env.hcl` and replace `state_bucket` / `lock_table` with the existing Terraform
   state bucket and DynamoDB lock table for that environment; check `aws_region`.
2. Set `db_host` / `db_port` in `aws/glue/<env>.tfvars` and pass the credentials at apply time with
   `TF_VAR_db_username` / `TF_VAR_db_password` (never commit them). `aws/glue/secrets.tf` stores them
   as JSON (`host, port, dbname, username, password`) — the same shape the framework reads through
   `FRAMEWORK_DB_SECRET_NAME`.
3. If Postgres is only reachable inside a VPC, set the VPC connection variables in
   `aws/glue/<env>.tfvars`.

## Deploying

```bash
cd infra/dev/us-east-1
terragrunt plan
terragrunt apply
```

Repeat under `infra/test/us-east-1` and `infra/prod/us-east-1`. Each environment is independent
(separate bucket, secret, job and state path).

## Destroying

```bash
cd infra/<env>/us-east-1
terragrunt destroy
```
