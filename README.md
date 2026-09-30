# CMS Compliance Framework

A project-agnostic, filename-driven framework for CMS compliance source files: batch registration,
file intake, validation, promotion to core, manual overrides (reuse, late arrival, correction) and
**closing each batch** after its SLA hold. The extract is produced by a separate process that reads
the batches. PostgreSQL + Python; runs locally or as AWS Glue jobs.

| Start here | |
|---|---|
| Setup, settings, commands, onboarding | [`docs/framework-package.md`](docs/framework-package.md) |
| What each module does | [`docs/module-reference.md`](docs/module-reference.md) |
| Design and decisions (v6) | [`docs/design/cms-compliance-framework-design.md`](docs/design/cms-compliance-framework-design.md) |
| Schema (source of truth) | [`src/framework/sql/schema.sql`](src/framework/sql/schema.sql) |

**What lives where (v6)**

| In tables (6 reference / configuration) | Outside tables |
|---|---|
| `ComplianceProject`, `ComplianceSourceSystem`, `ComplianceRunType` (incl. `SLA_Days`, `Carry_Fwd_Ind`), `ComplianceDataSetSourceXwalk` (which project/table/source/run type apply, and when), `ComplianceSourceFileConfig` (file contract, paths, targets, recipients), `ComplianceRuleBinding` (file rules at project / table / run type / source level) | Database connection: `.env` locally, Secrets Manager in AWS · Settings: `--set` job arguments > environment > `.env` · Report period: `run --module BATCH_CREATION --period <NAME>` (`period_sql.py`) · Event vocabulary: `audit.py` |

```bash
pip install -e ".[dev]"
cp .env.example .env                      # database + local settings
framework init-db
framework list-modules                                                       # every module, its parameters
framework run --module BATCH_CREATION --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH
framework run --module BATCH_CREATION --project PRJA                         # that project's ad-hoc requests only
framework run --module FILE_LOAD --bucket inbound --key prja/in/PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt
framework run --module FILE_LOAD                                              # sweep every configured inbound location
framework close-batches --project PRJA                                        # SLA sweep: closes batches with data
framework close-batch --btch-id <Btch_ID> --closed-by jdoe                    # a person closes a batch without data
```

Every batch-creation and file-loading job is started through `run --module NAME` (modules.py) - one
entry point per concern, the name picks what runs, and everything is scoped to `--project` where that
applies. There is no separate `create-batches` / `process-intake` / `ingest-file` / `ingest-path`
command; `BATCH_CREATION` and `FILE_LOAD` are it.

---

## AWS deployment

Everything runs on AWS as one Glue runner job (one module per run), one Step Functions workflow per
project (runs the requested steps in order, alerts with the project on failure) and EventBridge
scheduled rules (each step at its own time; daily / weekly / monthly runs; manual runs any time),
deployed with Terraform / Terragrunt from [`module/aws/compliance_frameworks/compliance_batch_framework`](module/aws/compliance_frameworks/compliance_batch_framework/README.md)
as two Terragrunt submodules (`glue`, `stepfunctions`) that the team's GitHub Actions deploy workflow
applies; see its README for the runbook. `scripts/` rebuilds its framework wheel, starts workflows by hand
and tests its Terraform (`tests/terraform/`); `docs/deployer/` holds the deployer role template.
