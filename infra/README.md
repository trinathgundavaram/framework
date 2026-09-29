# infra/ — Terragrunt live configuration

Per-environment Terragrunt configs that apply the AWS deployment in
[`module/aws/compliance_frameworks/compliance_batch_framework`](../module/aws/compliance_frameworks/compliance_batch_framework/README.md)
(all Glue jobs of the framework). That README is the runbook; this file only covers the Terragrunt side.
In the team infra repository the module lives at the same path with that repository's own live config.

```
infra/
  terragrunt.hcl                  Root config: remote state + AWS provider, shared by every environment
  dev|test|prod/env.hcl           Per-env settings: region, existing state bucket / lock table
  dev|test|prod/us-east-1/terragrunt.hcl
                                  Applies the module with its <env>.tfvars
```

```bash
module/aws/compliance_frameworks/compliance_batch_framework/build_artifacts.sh .   # framework wheelhouse
cd infra/dev/us-east-1
terragrunt plan
terragrunt apply
```

Fill in `infra/<env>/env.hcl` (state bucket, lock table, region) first; pass the database
credentials with `TF_VAR_db_username` / `TF_VAR_db_password`.
