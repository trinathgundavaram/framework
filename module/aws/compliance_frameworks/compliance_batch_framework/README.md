# compliance_batch_framework — AWS deployment

Runs the compliance batch framework (the `cms-compliance-framework` Python package) on AWS:

```
EventBridge scheduled rules (UTC)               one rule per schedule entry
   ODR  daily_batches  12:00 UTC   {steps: [BATCH_CREATION], run_type: DAILY, period: PREV_DAY}
   ODR  file_load      every 15'   {steps: [FILE_LOAD, OVERRIDE_DECISIONS, NOTIFY]}
   ODR  close          hourly      {steps: [BATCH_CLOSE, NOTIFY]}
        │                                        manual: src/run_workflow.sh dev ODR FILE_LOAD,BATCH_CLOSE
        ▼
Step Functions  compliance_batch_framework_ODR_dev      one workflow per project (+ all_projects)
   validate the steps -> run them one at a time -> on any failure stop and email the project
        │  one Glue run per step
        ▼
Glue  compliance_batch_framework_runner_dev             ONE job for every project and module
   framework run --module <STEP> --project ODR [--run-type --period --table --as-of]
        │
        ▼
existing RDS PostgreSQL (VPC)  +  S3 inbound / archive / quarantine  +  SES email
```

- **Two Glue jobs in total.** `runner` runs every module for every project (one module, one project per
  run); `metadata_load` loads configuration CSVs you drop in S3.
- **One Step Functions workflow per project**, all generated from one template. An execution runs the
  steps named in its input, in order; the first failure stops it.
- **Schedules are separate from the workflow.** Each schedule has its own time and input
  (steps, run type, period), so each step can run at its own time, and monthly / weekly / daily runs share
  the workflow. Any steps can be run by hand at any time.

Deployed as two Terragrunt submodules by the team's GitHub Actions workflow, each with its own
`env-config/us-east-1/` (`common.tfvars` + `<env>.tfvars`). Every resource name starts with `compliance`;
every resource carries the `required_common_tags` of `common.tfvars` plus `Environment` and `ManagedBy`
(per-project resources add `Project`).

## Steps

| Step | Framework module | Default Glue size / timeout | A "completed with problems" result… |
|---|---|---|---|
| `BATCH_CREATION` | routine batches for `run_type` / `period`, and the project's ad-hoc intake requests | 0.0625 DPU / 60 min | stops the workflow (configuration problem) |
| `FILE_LOAD` | every file waiting in the project's inbound folders | 1 DPU / 120 min | continues: a bad file is audited, retried next sweep and emailed once |
| `OVERRIDE_DECISIONS` | apply / expire approved REUSE overrides | 0.0625 DPU / 30 min | continues: an invalid override is audited and emailed once |
| `BATCH_CLOSE` | close batches past their SLA hold that have data or are in exception (`run_type` / `table` narrow it) | 0.0625 DPU / 30 min | stops the workflow |
| `NOTIFY` | email the project's pending events | 0.0625 DPU / 30 min | stops the workflow |

A framework error (exit 2: database unreachable, bad configuration, unknown period) always stops the
workflow. Sizes, timeouts and the "problems" behaviour are `step_settings`. Every step is idempotent and
safe to run concurrently with itself — see *Fallbacks*.

## Configuring projects and schedules

Projects and their schedules are `local.projects` in `stepfunctions/_local.tf` (the same in every
environment); who is emailed is `project_alert_emails` in `stepfunctions/env-config/us-east-1/<env>.tfvars`.

```hcl
projects = {
  UNIVERSE = {
    settings = { FILE_RULES_MODE = "ANNOTATE" }                # framework settings for this project only
    schedules = {
      monthly_batches = { expression = "cron(0 12 1 * ? *)",  steps = ["BATCH_CREATION"], run_type = "MONTHLY", period = "PREV_CALENDAR_MONTH" }
      weekly_batches  = { expression = "cron(0 12 ? * MON *)", steps = ["BATCH_CREATION"], run_type = "WEEKLY",  period = "PREV_CALENDAR_WEEK" }
      daily_batches   = { expression = "cron(0 12 * * ? *)",  steps = ["BATCH_CREATION"], run_type = "CMS",     period = "CURRENT_CALENDAR_MONTH" }
      file_load       = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close           = { expression = "cron(30 4 * * ? *)",   steps = ["BATCH_CLOSE", "NOTIFY"] }
    }
  }
  all_projects = {                                             # steps run without --project
    schedules = { notify_unscoped = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] } }
  }
}
```

- `expression`: `cron(min hour day month weekday year)` or `rate(15 minutes)`, **in UTC** (EventBridge
  rules have no time zone). Chicago is UTC-5 in summer and UTC-6 in winter:

  | Wanted (Chicago) | UTC expression | Runs at |
  |---|---|---|
  | 06:00 daily | `cron(0 12 * * ? *)` | 07:00 CDT / 06:00 CST |
  | 06:00 on the 1st | `cron(0 12 1 * ? *)` | same, on the 1st in both |
  | 06:00 Monday | `cron(0 12 ? * MON *)` | same, on Monday in both |
  | 23:30 daily | `cron(30 4 * * ? *)` | 23:30 CDT / 22:30 CST |
  | every 15 minutes / hourly | `cron(0/15 * * * ? *)` / `cron(0 * * * ? *)` | unaffected |

  Keep daily times out of 05:00–06:59 UTC (Chicago midnight) so the run's business date never changes
  with daylight saving. One-time runs: use `src/run_workflow.sh`.
- Optional per schedule: `run_type`, `period` (a name in the framework's `period_sql.py`), `table`,
  `as_of`, `enabled = false`. A project with `enabled = false` keeps its workflow for manual runs.
- Adding a project = adding an entry to `local.projects` (and its emails to `project_alert_emails`) and
  deploying `stepfunctions`: a new workflow, schedule rules and alert topic; no new Glue job.
- Keep the `all_projects` hourly `NOTIFY`: it emails events that belong to no project (e.g.
  `CONFIG_VALIDATION_FAILED`). `plan` warns if it is missing.

## Metadata database and schema

Both are inputs of the Glue jobs, set per environment in `glue/env-config/us-east-1/<env>.tfvars`:

| tfvars | Glue job argument | Meaning |
|---|---|---|
| `metadata_schema` | `--FRAMEWORK_METADATA_SCHEMA` (runner), `--METADATA_SCHEMA` (metadata_load) | PostgreSQL schema holding the framework tables (default `cms_compliance`) |
| `rds_database_name` | `--FRAMEWORK_DB_NAME` (runner), `--RDS_DATABASE_NM` (metadata_load) | PostgreSQL database; `null` = the `dbname` of the secret |

They can also be given for one run: `metadata_schema` / `metadata_db` in a workflow's input
(`run_workflow.sh --metadata-schema S --metadata-db DB`, or a schedule entry of `local.projects`), or the
job arguments above on a direct Glue run. A run's value wins over the project's settings and the job
default. `metadata_load` adds the schema to a `--TABLE_NAME` that has none.

## Environment in database names (`$env`)

The staging and core database (schema) names and the S3 paths of `ComplianceSourceFileConfig` can carry
a `$env` token, so one set of configuration rows works in every environment (the GRE convention):

| Authored | DEV | TEST | QA | PROD / UAT |
|---|---|---|---|---|
| `CMS_STG_$ENV` | `CMS_STG_DEV` | `CMS_STG_TEST` | `CMS_STG_QA` | `CMS_STG` |
| `cms_core_$env_t` | `cms_core_dev_t` | `cms_core_test_t` | `cms_core_qa_t` | `cms_core_t` |
| `s3://inbound-$env/odr/in/` | `s3://inbound-dev/odr/in/` | `s3://inbound-test/odr/in/` | `s3://inbound-qa/odr/in/` | `s3://inbound-/odr/in/` |

- Columns: `Stg_Schema_Nm`, `Core_Schema_Nm`, `S3_Src_File_Path`, `Src_File_Archive_Path`.
- The token matches in any casing and is replaced in its own casing (`$env` → `dev`, `$ENV` → `DEV`,
  `$Env` → `Dev`). PROD and UAT replace it with nothing; a doubled underscore left in a database name
  collapses (paths are left as they are, so prefer a database-style name where PROD has no suffix).
- The environment is the `ENVIRONMENT` setting (the Glue jobs get `--FRAMEWORK_ENVIRONMENT` = the deploy `env`, upper-cased); `ENV_VALUE` overrides the replacement text for
  that environment (e.g. `ENV_VALUE=uat` where UAT databases do carry a suffix).

## Running steps by hand

```bash
src/run_workflow.sh dev ODR FILE_LOAD,BATCH_CLOSE                               # now, in this order
src/run_workflow.sh dev UNIVERSE BATCH_CREATION --run-type MONTHLY --period PREV_CALENDAR_MONTH
src/run_workflow.sh prod ODR BATCH_CREATION --run-type DAILY --period PREV_DAY --as-of 2026-09-01   # missed run
src/run_workflow.sh dev all_projects NOTIFY
src/run_workflow.sh dev ODR FILE_LOAD --metadata-schema cms_compliance_v2 --metadata-db compliance   # another metadata schema / database
```

Or, in the Step Functions console, **Start execution** on `compliance_batch_framework_<PROJECT>_<env>` with
input like `{"steps": ["FILE_LOAD", "BATCH_CLOSE"], "run_type": "DAILY"}`. The execution history shows,
per project, every run, its input and each step's Glue run.

Commands that are not steps run on the runner job directly:

```bash
R=compliance_batch_framework_runner_dev
aws glue start-job-run --job-name $R --arguments '{"--FW_ARGS": "validate-config"}'
aws glue start-job-run --job-name $R --arguments \
  '{"--FW_ARGS": "close-batch --btch-id 20260201_ODR_ROPENS_50_DAILY_2026_1 --closed-by jdoe"}'
aws glue start-job-run --job-name $R --arguments '{"--FW_ARGS": "health"}'
```

Output goes to CloudWatch Logs (`/aws-glue/python-jobs/output` and `.../error`); the first line of every
run names the module and project.

## Failure alerts

| What | Who is told | How |
|---|---|---|
| A step fails, or the input names an unknown step | the project's `project_alert_emails` + `alert_email` | the workflow publishes: subject `[FAILED] project ODR dev: StepFailed`, body with the schedule (or "manual run"), the failed step (`step BATCH_CLOSE (2 of 3)`), the Glue error and a link to the execution |
| An execution times out (`workflow_timeout_hours`) or is aborted | same | EventBridge rule on the workflow's `TIMED_OUT` / `ABORTED` events |
| A schedule cannot start its workflow (after 1 hour of retries) | `alert_email` | a CloudWatch alarm on the rule's `FailedInvocations` emails |
| A Glue run fails, times out or errors (also runs started by hand: `validate-config`, `metadata_load`) | glue `alert_emails` | EventBridge rule `..._glue_failed_<env>` → SNS `..._glue_alerts_<env>` |
| Business events: quarantined file, missing source, invalid override, technical failure of a file | the file config's recipients / `DEFAULT_NOTIFY_EMAILS` | the framework's `NOTIFY` step (SES) |

SNS email subscriptions must be confirmed once from the email SNS sends.

## Fallbacks and safeguards

| Situation | What happens |
|---|---|
| An email cannot be sent (SES error, throttling) | The event stays unsent and is retried by the next `NOTIFY`; the step exits 1, so the workflow alerts through SNS |
| Two runs overlap (a manual run during a schedule, a slow 15-minute cycle) | Every step is safe to overlap: a file being loaded is locked (the other run reports it `IN_PROGRESS`), batches are locked while promoted or closed, `NOTIFY` claims each email with `SKIP LOCKED` (no duplicates) |
| Two projects share an inbound folder | A project's `FILE_LOAD` only takes files matching its own templates there; unmatched files are quarantined only in a folder that belongs to one project alone (and left for the all-projects sweep otherwise) |
| One file keeps failing technically | Recorded `FAILED_TECHNICAL`, retried by every sweep, reported once (`FILE_TECHNICAL_FAILURE`), listed by `health`; the rest of the workflow carries on |
| Too many Glue runs at once (`runner_max_concurrent_runs`, Glue throttling) | The step retries with backoff (5 attempts from 1 minute) |
| A Glue run fails | The workflow stops and alerts; the next scheduled run tries again (steps are idempotent) |
| A missed or failed schedule | Re-run by hand with `--as-of <date>` |
| Schedule delivered twice (at-least-once) | Harmless: every step is idempotent |
| Daylight saving | Schedules are UTC, so local run times move by an hour; batch dates use `BUSINESS_TZ` (America/Chicago) |
| Unknown step name | `plan` refuses it in a schedule; the workflow refuses it (and alerts) for a manual run |

## Layout and submodules

```
compliance_batch_framework/
  src/                                        framework source, docs, wheel build (not deployed)
  code/                                       uploaded to S3 by the glue submodule (see *S3 layout*)
    glue_framework_entry.py                   runner entry point (Glue arguments -> `framework` CLI)
    glue_job_metadata_load.py  rds_conn.py    configuration-load job
    wheels/cms_compliance_framework-*.whl     the framework, built from src/ (committed)
  glue/
    env-config/us-east-1/                       common.tfvars (tags) + dev / test / prod.tfvars (secret, data buckets, emails)
    main.tf          code upload, runner and metadata_load jobs
    iam.tf           Glue role
    cloudwatch.tf    failed-run rule, SNS alert topic
    _variable.tf  _local.tf  _data.tf  outputs.tf  backend.tf  terragrunt.hcl
  stepfunctions/
    env-config/us-east-1/                       common.tfvars (tags) + dev / test / prod.tfvars (alert emails, schedules on/off)
    main.tf          one workflow per project (workflow.asl.json.tftpl)
    iam.tf           workflow and EventBridge roles
    schedules.tf     EventBridge schedule rules
    cloudwatch.tf    SNS topics, stopped-execution rules, failed-schedule alarms
    _variable.tf  _local.tf  _data.tf  outputs.tf  backend.tf  terragrunt.hcl
```

`backend.tf` declares the S3 backend (`backend "s3" {}`); the repository root's `remote_state` fills in
bucket, key and lock table. If the root instead *generates* `backend.tf`, delete these two files.

| Submodule | Creates | Needs first |
|---|---|---|
| `glue` | role `compliance_batch_framework_glue_<env>`, jobs `..._runner_<env>` / `..._metadata_load_<env>`, S3 objects, rule `..._glue_failed_<env>`, SNS `..._glue_alerts_<env>` | — |
| `stepfunctions` | roles `..._workflow_<env>` / `..._events_<env>`, workflows `..._<PROJECT>_<env>`, rules `..._<PROJECT>_<env>_<schedule>` + failure alarms, `..._stopped` rules, SNS `..._<PROJECT>_<env>_failures` / `..._ops_<env>` (log groups only with `enable_workflow_logs`) | `glue` |

Existing resources it uses (not managed here): the artifacts bucket, the RDS database and its Secrets
Manager secret (`host, port, username, password, dbname`), the Glue connection(s) to the RDS VPC, the
data buckets, the SES sender.

Helpers in `src/`: `build_wheel.sh` and the GitHub Actions workflow `github-workflow/build-framework-wheel.yaml`
(rebuild `code/wheels/`), `run_workflow.sh` (start a workflow by hand).

## S3 layout

Everything the jobs read from the artifacts bucket sits under one prefix, classified by type
(`glue/_local.tf`):

```
s3://silverton-maa-global-artifactory-<env>/compliance_batch_framework/
  glue/glue_framework_entry.py        runner job script                 <- code/glue_framework_entry.py
  glue/glue_job_metadata_load.py      metadata_load job script          <- code/glue_job_metadata_load.py
  glue/rds_conn.py                    its connection helper             <- code/rds_conn.py
  whl/cms_compliance_framework-*.whl  the framework (--extra-py-files)  <- code/wheels/*.whl
  config_data/*.csv                   configuration CSVs for metadata_load (uploaded by hand)
  tmp/                                Glue --TempDir
```

The `glue` submodule uploads `glue/` and `whl/` on every apply; the Glue role can read the whole prefix
and write only `tmp/`. Inbound, archive and quarantine files are not here: they live in the data
buckets named by `ComplianceSourceFileConfig` and `quarantine_uri` (`data_bucket_names`).

## Deployer role (gov-compliance-it-deployer)

Everything this module creates is named `compliance*` and falls inside the role's policies:
IAM roles `role/compliance*` (create, inline policy, attach, pass), Glue jobs `job/*compliance*`, state
machines `stateMachine:compliance*`, SNS topics `compliance*`, EventBridge rules `rule/compliance*`,
CloudWatch alarms, and S3 objects in `silverton-maa-global-artifactory-<env>`. `local.name_prefix`
(`compliance_batch_framework`) must keep starting with `compliance`.

Not granted by the role, so not used: EventBridge Scheduler (`scheduler:*`) and SQS (`sqs:*`) — hence
UTC EventBridge rules and alarms instead of time-zone schedules and a dead-letter queue — and the
`logs:*LogDelivery` actions on `*` that Step Functions needs for CloudWatch Logs (`enable_workflow_logs`
stays `false`; execution history and the failure emails do not depend on it).

`src/docs/deployer/gov-compliance-it-deployer.original.yml` is the role template as provided;
`src/docs/deployer/gov-compliance-it-deployer.yml` is the same role with duplicates removed and a
`MaaGOVCOMPLIANCEITScheduler` policy (Scheduler, SQS, Step Functions log delivery on `compliance*`) that
enables time-zone schedules, a dead-letter queue and workflow logs once it is deployed.

## Deploying (GitHub Actions)

Add to `deploy-<env>.yaml`: module option `compliance_frameworks/compliance_batch_framework`; the
submodule options `glue` and `stepfunctions` already exist. Leave the submodule empty to apply both
(`glue` first).

Each submodule's `terragrunt.hcl` includes the repository root (`find_in_parent_folders()`) for remote
state and provider, and adds its own `env-config/${TF_VAR_region}/common.tfvars` and `${TF_VAR_env}.tfvars`
(the workflow sets `TF_VAR_env` and `TF_VAR_region`).
No credentials are passed: the jobs read the database secret at run time.

Before the first deploy, fill in every `REPLACE_WITH_...`: the Glue connection name in `glue/_local.tf`
and the values in `glue/env-config/us-east-1/<env>.tfvars` (database secret, data buckets, quarantine
path, emails) and `stepfunctions/env-config/us-east-1/<env>.tfvars` (alert emails). Everything else
(names, S3 keys, packages, schema, time zone, projects and schedules) is in each submodule's `_local.tf`.
Locally:

```bash
export TF_VAR_env=dev
cd glue && terragrunt plan
```

Packages: Python shell jobs run Python 3.9 (`python_version`; `glue_version` does not apply to them).
The framework wheel is uploaded from `code/wheels/` and attached with `--extra-py-files` (Python shell
cannot install S3 packages through `--additional-python-modules`); `local.pypi_packages` (`glue/_local.tf`) install from PyPI
through `--additional-python-modules`, like the team's other Glue jobs. Database connections require SSL.

### First run in a new environment

1. The framework tables already exist: they are created separately from `src/framework/sql/schema.sql`
   in the `metadata_schema` of the metadata database (the framework never creates or alters tables).
2. Load the configuration tables (below): project, source system, run type, crosswalk, file config,
   rule binding. The project's staging and core tables are created by the project (their framework
   columns: `docs/framework-package.md`, Onboarding).
3. `validate-config` on the runner — must report no errors.
4. Confirm the SNS subscription emails. `enable_schedules = false` keeps the schedules off until you
   are ready.

## Loading the configuration tables (metadata-load job)

Upload the CSVs to `s3://silverton-maa-global-artifactory-<env>/compliance_batch_framework/config_data/`; one run upserts one CSV
into one table (the header is the column list; audit columns are skipped).

```bash
J=compliance_batch_framework_metadata_load_dev
load() {  # table, file, primary key
  aws glue start-job-run --job-name $J --arguments \
    "{\"--TABLE_NAME\": \"$1\", \"--S3_FILE_NAME\": \"$2\", \"--PRIMARY_KEY\": \"$3\"}"
}
load complianceproject            compliance_project.csv              project_cd
load compliancesourcesystem       compliance_source_system.csv        src_id
load complianceruntype            compliance_run_type.csv             run_ty
load compliancedatasetsourcexwalk compliance_dataset_source_xwalk.csv project_cd,table_nm,src_id,run_ty,effective_start_dt_key
```

`ComplianceSourceFileConfig` (identity `cfg_id`: omit it and add `"--MODE": "insert_only"`) and
`ComplianceRuleBinding` (`project_cd,table_nm,src_id,run_ty,gre_rule_group,gre_rule_variant`) load the
same way. Run `validate-config` after every change.

## Updating

- **Framework code**: edit `src/framework/`, rebuild the wheel (the *Build Compliance Framework Wheel*
  workflow, or `src/build_wheel.sh`), deploy `glue`. A changed `sql/schema.sql` is applied to the
  database separately; the framework never alters tables.
- **Schedules / projects**: edit `local.projects` in `stepfunctions/_local.tf`, deploy `stepfunctions`.
- **Job settings**: edit `glue/env-config/us-east-1/<env>.tfvars` (or `glue/_local.tf` for S3 keys and
  packages), deploy `glue`.
- **Tags**: edit `env-config/us-east-1/common.tfvars` in both submodules.

## The framework wheel

`code/wheels/cms_compliance_framework-<version>-py3-none-any.whl` is `src/framework/` packaged for pip; Glue
installs it on every run, so a rebuilt wheel takes effect on the next run after `glue` is deployed. How
to edit, rebuild (GitHub Actions or locally) and inspect it: [`src/README.md`](src/README.md).
