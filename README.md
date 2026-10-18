# CMS Compliance Framework

Everything lives in [`module/aws/compliance_frameworks/compliance_batch_framework`](module/aws/compliance_frameworks/compliance_batch_framework/README.md),
ready to copy into the infrastructure repository:

| Folder | |
|---|---|
| `src/` | the framework's Python source, docs and wheel build ([`src/README.md`](module/aws/compliance_frameworks/compliance_batch_framework/src/README.md)) |
| `code/` | Glue scripts and the built framework wheel |
| `glue/`, `stepfunctions/` | Terragrunt submodules deployed by the GitHub Actions deploy workflow |

[`airflow/`](airflow_resources/docs/AIRFLOW_DEPLOYMENT.md) is a separate, self-contained copy of the
framework that runs from Airflow DAGs with its tables in Teradata. Its DAG file and package are in `airflow/`; its
documentation (deployment guide, operational runbook, open items), DDL, CMSUNIV Variables and metadata, and
requirements are in `airflow_resources/`.
