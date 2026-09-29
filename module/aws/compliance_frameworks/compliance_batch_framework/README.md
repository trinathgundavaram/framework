# compliance_batch_framework — AWS deployment

Runs the compliance batch framework (the `cms-compliance-framework` Python package) on AWS:

```
EventBridge Scheduler (America/Chicago)          one schedule group per project, one schedule per entry
   ODR  daily_batches  06:00       {steps: [BATCH_CREATION], run_type: DAILY, period: PREV_DAY}
   ODR  file_load      every 15'   {steps: [FILE_LOAD, OVERRIDE_DECISIONS, NOTIFY]}
   ODR  close          hourly      {steps: [BATCH_CLOSE, NOTIFY]}
        │                                        manual: ./run_workflow.sh dev ODR FILE_LOAD,BATCH_CLOSE
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
  run); `metadata_load` loads the configuration CSVs by hand.
- **One Step Functions workflow per project**, all generated from one template. An execution runs the
  steps named in its input, in order; the first failure stops it.
- **Schedules are separate from the workflow.** Each schedule has its own time, time zone and input
  (steps, run type, period), so each step can run at its own time, and monthly / weekly / daily runs share
  the workflow. Any steps can be run by hand at any time.

Specific Terraform for this deployment, applied per environment with the co-located `.tfvars` through
Terragrunt — the conventions of the team's other Glue jobs (`module/aws/part c odr/glue-jobs/`).

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

```hcl
projects = {
  UNIVERSE = {
    alert_emails = ["universe-team@example.com"]
    settings     = { FILE_RULES_MODE = "ANNOTATE" }            # framework settings for this project only
    schedules = {
      monthly_batches = { expression = "cron(0 6 1 * ? *)",   steps = ["BATCH_CREATION"], run_type = "MONTHLY", period = "PREV_CALENDAR_MONTH" }
      weekly_batches  = { expression = "cron(0 6 ? * MON *)", steps = ["BATCH_CREATION"], run_type = "WEEKLY",  period = "PREV_CALENDAR_WEEK" }
      daily_batches   = { expression = "cron(0 6 * * ? *)",   steps = ["BATCH_CREATION"], run_type = "CMS",     period = "CURRENT_CALENDAR_MONTH" }
      file_load       = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close           = { expression = "cron(30 23 * * ? *)",  steps = ["BATCH_CLOSE", "NOTIFY"] }
      one_off         = { expression = "at(2026-10-01T06:00:00)", steps = ["BATCH_CREATION"], run_type = "ADHOC" }
    }
  }
  all_projects = {                                             # steps run without --project
    schedules = { notify_unscoped = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] } }
  }
}
```

- `expression`: `cron(min hour day month weekday year)`, `rate(15 minutes)` or `at(yyyy-mm-ddThh:mm:ss)`
  (one-time), in `schedule_timezone` (daylight saving handled).
- Optional per schedule: `run_type`, `period` (a name in the framework's `period_sql.py`), `table`,
  `as_of`, `enabled = false`. A project with `enabled = false` keeps its workflow for manual runs.
- Adding a project = adding an entry and `terragrunt apply`: a new workflow, schedule group and alert
  topic; no new Glue job.
- Keep the `all_projects` hourly `NOTIFY`: it emails events that belong to no project (e.g.
  `CONFIG_VALIDATION_FAILED`). `plan` warns if it is missing.

## Running steps by hand

```bash
./run_workflow.sh dev ODR FILE_LOAD,BATCH_CLOSE                               # now, in this order
./run_workflow.sh dev UNIVERSE BATCH_CREATION --run-type MONTHLY --period PREV_CALENDAR_MONTH
./run_workflow.sh prod ODR BATCH_CREATION --run-type DAILY --period PREV_DAY --as-of 2026-09-01   # missed run
./run_workflow.sh dev all_projects NOTIFY
```

Or, in the Step Functions console, **Start execution** on `compliance_batch_framework_<PROJECT>_<env>` with
input like `{"steps": ["FILE_LOAD", "BATCH_CLOSE"], "run_type": "DAILY"}`. The execution history shows,
per project, every run, its input and each step's Glue run.

Commands that are not steps run on the runner job directly:

```bash
R=compliance_batch_framework_runner_dev
aws glue start-job-run --job-name $R --arguments '{"--FW_ARGS": "init-db"}'
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
| A step fails, or the input names an unknown step | the project's `alert_emails` + `alert_email` | the workflow publishes: subject `[FAILED] project ODR dev: StepFailed`, body with the schedule (or "manual run"), the failed step (`step BATCH_CLOSE (2 of 3)`), the Glue error and a link to the execution |
| An execution times out (`workflow_timeout_hours`) or is aborted | same | EventBridge rule on the workflow's `TIMED_OUT` / `ABORTED` events |
| A schedule cannot start its workflow (after 1 hour of retries) | `alert_email` | the event lands on the schedule dead-letter queue; a CloudWatch alarm emails |
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
| Daylight saving | Handled by `schedule_timezone`; a time in the skipped spring hour runs at the next valid time |
| Unknown step name | `plan` refuses it in a schedule; the workflow refuses it (and alerts) for a manual run |

## What gets deployed (per environment)

| Resource | Purpose |
|---|---|
| Glue job `<prefix>_runner_<env>` | every framework module / command (Python Shell 3.9) |
| Glue job `<prefix>_metadata_load_<env>` | seed CSV → configuration table |
| Step Functions `<prefix>_<PROJECT>_<env>` + log group | one per project (+ `all_projects`) |
| Scheduler group `<prefix>-<PROJECT>-<env>` + schedules | the project's schedules |
| SQS `<prefix>_schedule_dlq_<env>` + alarm | schedules that could not start |
| SNS `<prefix>_<PROJECT>_<env>_failures`, `<prefix>_ops_<env>` | failure emails |
| S3 artifacts bucket | scripts, wheelhouse, DDL copy, seed CSVs; TLS only, old versions and Glue temp files expire |
| Secrets Manager secret | RDS credentials (`host, port, dbname, username, password`) |
| IAM roles | Glue (artifacts, data buckets, secret, SES), Step Functions (runner, SNS, logs), Scheduler (start workflows, DLQ) |
| Glue NETWORK connection | runs the jobs in the RDS VPC |

`<prefix>` is `compliance_batch_framework`.

## Layout

```
compliance_batch_framework/
  README.md                  this runbook
  build_artifacts.sh         builds dist/: framework wheel + dependencies (and pg8000) for Glue (Python 3.9, x86_64)
  run_workflow.sh            start a project's workflow by hand
  test_module.sh             terraform fmt / validate / test with a mocked AWS provider (no credentials)
  variables.tf  locals.tf    inputs; projects -> workflows and schedules; framework settings
  glue-runner-job.tf         the runner job
  glue-metadata-load-job.tf  the configuration-load job
  stepfunctions.tf           one workflow per project + role + logs
  stepfunctions/workflow.asl.json.tftpl   the workflow (JSONata)
  scheduler.tf               schedule groups, schedules, dead-letter queue, role
  alerts.tf                  SNS topics, subscriptions, stopped-execution rules, DLQ alarm
  s3-artifacts.tf  secrets.tf  iam.tf  outputs.tf
  dev.tfvars test.tfvars prod.tfvars
  code/
    glue_framework_entry.py  runner entry point (Glue arguments -> `framework` CLI)
    glue_job_metadata_load.py + rds_conn.py, seed_data/*.csv
  tests/module.tftest.hcl    mocked-provider tests (run by test_module.sh)
  dist/                      (built, git-ignored) wheelhouse/*.whl, schema.sql
```

## Prerequisites

- Terraform >= 1.5 (1.7+ for `test_module.sh`), Terragrunt >= 0.55, Python 3 with pip.
- AWS credentials with permission to manage S3, Secrets Manager, IAM, Glue, Step Functions, EventBridge
  Scheduler, EventBridge, SQS, SNS, CloudWatch.
- The existing Terraform state bucket and DynamoDB lock table per environment.
- **Network** — the private subnet the Glue jobs run in must reach RDS on 5432, S3 (gateway endpoint),
  Secrets Manager and SES (NAT gateway or interface endpoints). No PyPI access is needed: both jobs
  install from the wheelhouse. The security group needs a self-referencing inbound rule (Glue
  requirement) and outbound 443 + 5432. Database connections require SSL.
- **SES** — `notify_from_email` verified (and SES out of the sandbox, or the recipients verified).
- **Data buckets** — the inbound / archive buckets of `ComplianceSourceFileConfig` and the quarantine
  bucket exist; list them in `data_bucket_names`.

## Deploying

```bash
# once per environment: fill in every REPLACE_WITH_... in <env>.tfvars and the Terragrunt env.hcl
export TF_VAR_db_username=framework_app
export TF_VAR_db_password='...'

./build_artifacts.sh <path to the framework repository>   # again whenever the framework code changes
./test_module.sh                                           # optional: validate + mocked tests
cd <live config>/<env>/us-east-1 && terragrunt plan && terragrunt apply
```

`plan` stops with *"dist/wheelhouse is empty"* if the build was skipped.

### First run in a new environment

1. `init-db` on the runner (above) — creates the metadata schema.
2. Load the configuration tables with `metadata_load`, in order: project, source system, run type,
   crosswalk, file config, rule binding (below). The project's staging and core tables are created by
   the project (their framework columns: `docs/framework-package.md`, Onboarding).
3. `validate-config` on the runner — must report no errors.
4. Confirm the SNS subscription emails. The schedules are already running (`enable_schedules = false`
   in the tfvars keeps them off until you are ready).

## Loading the configuration tables (metadata-load job)

One run upserts one CSV into one table; the CSV header is the column list (audit columns excluded).

```bash
J=compliance_batch_framework_metadata_load_dev
INPUT=s3://<artifacts-bucket>/compliance_batch_framework/seed_data/
load() {  # table, file, primary key
  aws glue start-job-run --job-name $J --arguments \
    "{\"--TABLE_NAME\": \"cms_compliance.$1\", \"--S3_INPUT_PATH\": \"$INPUT\",
      \"--S3_FILE_NAME\": \"$2\", \"--PRIMARY_KEY\": \"$3\"}"
}
load complianceproject            compliance_project.csv              project_cd
load compliancesourcesystem       compliance_source_system.csv        src_id
load complianceruntype            compliance_run_type.csv             run_ty
load compliancedatasetsourcexwalk compliance_dataset_source_xwalk.csv project_cd,table_nm,src_id,run_ty,effective_start_dt_key
```

`ComplianceSourceFileConfig` (identity `cfg_id`: omit it and use `--MODE insert_only` for new rows) and
`ComplianceRuleBinding` (`project_cd,table_nm,src_id,run_ty,gre_rule_group,gre_rule_variant`) load the
same way. A later change: edit the CSV, `terragrunt apply`, run the job for that table, run
`validate-config`.

## Updating

- **Framework code**: `./build_artifacts.sh <repo>` then `terragrunt apply` — the next Glue run installs
  the new wheels. A changed schema needs the database re-created (`init-db` only creates a missing
  schema).
- **Schedules / projects / settings**: edit the tfvars and apply.

## Destroying

`terragrunt destroy`. The artifacts bucket has `force_destroy` only outside prod. RDS, its data and the
data buckets are not managed here and are never touched.
