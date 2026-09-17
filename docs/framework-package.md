# CMS Compliance Framework: Python Package

Implementation of [`design/cms-compliance-framework-design.md`](design/cms-compliance-framework-design.md) (v4). File-by-file detail: [`module-reference.md`](module-reference.md).

- **Project-agnostic:** projects, tables, sources and run types are rows in five configuration tables. The code has no project-specific branches.
- **Filename-driven:** incoming files are recognised by the templates in `ComplianceSourceFileConfig`.
- **Job-level settings:** connections, runtime settings, the report period, the extract job and its parameters are **not** tables. They come from `.env` (local), AWS Secrets Manager (database credentials) and the arguments of each project's scheduled job.

## Layout

```
pyproject.toml            package metadata; `framework` console script
.env.example              sample local settings (copy to .env)
docker-compose.yml        optional local PostgreSQL 16 (not needed on Windows - see below)
src/framework/
  cli.py                  commands (one command = one service)
  app.py                  service wiring + health report
  settings.py             settings (job args > env > .env > default) and the database connection
  common.py               errors, clock, Btch_ID, Req_Stat values/transitions
  db.py                   init-db, advisory locks
  config.py               configuration rows, filename templates, validator
  period_sql.py           report-period SQL by name
  batches.py              create-batches, intake, CRC/extract rows
  ingest.py               file pipeline + resolution decision tables
  load.py                 file reading, staging (pandas/COPY or Spark), core swap, archive re-stage
  overrides.py            approvals, waivers, carry-forward, reopen promotion
  extract.py              eligibility, refresh/combine, trigger + close, SLA sweep
  audit.py                audit writer, notifications
  adapters.py             S3/local store, GRE rules engine, Glue/HTTP connectors, SES/SNS
  sql/schema.sql          schema (source of truth)
  sql/seed.sql            event vocabulary
  sql/approvals.sql       manual approval / waiver / carry-forward SQL
tests/                    unit + PostgreSQL integration tests
```

## Configuration

### Where values come from

| What | Source (highest precedence first) |
|---|---|
| Any setting | `--set NAME=VALUE` job argument > `FRAMEWORK_<NAME>` environment variable > `.env` file > built-in default |
| Database | `FRAMEWORK_DB_DSN` / `FRAMEWORK_DB_HOST`, `_PORT`, `_NAME`, `_USER`, `_PASSWORD`, `_SSLMODE`, `_CONNECT_TIMEOUT` (explicit values) > the Secrets Manager secret named by `FRAMEWORK_DB_SECRET_NAME` (JSON `host, port, dbname, username, password[, sslmode]`) |
| `.env` location | `--env-file`, else `FRAMEWORK_ENV_FILE`, else `./.env` (optional) |

- **Locally:** put the database values in `.env` (see `.env.example`). `.env` is git-ignored.
- **In AWS:** set only `FRAMEWORK_DB_SECRET_NAME` (the secret created by `aws/glue/secrets.tf` has the right shape); pass project settings as Glue job arguments (`--set ...`).
- **One database:** the framework schema (`METADATA_SCHEMA`) and the staging/core schemas named in `ComplianceSourceFileConfig` are in the same PostgreSQL database, so a promotion is one transaction.
- `framework show-config` prints every setting with its value and source (`argument`, `env`, `.env`, `default`) and the database target without the password.
- Direct connections only (D-55): no RDS Proxy / PgBouncer transaction pooling.

### Settings

The environment variable is `FRAMEWORK_<NAME>`; the job argument is `--set <NAME>=<value>` (case-insensitive).

| Setting | Default | Notes |
|---|---|---|
| `METADATA_SCHEMA` | `cms_compliance` | Schema of the framework tables. |
| `AWS_REGION` | `us-east-1` | |
| `BUSINESS_TZ` | `America/Chicago` | Run date, `Req_Dt_Key`, SLA hold. (Was `Business_Tz` on the crosswalk.) |
| `OBJECT_STORE`, `LOCAL_STORE_ROOT` | `s3`, `./.local_store` | `local` for development. |
| `DEFAULT_QUARANTINE_URI` | placeholder | Destination for files that match no config. |
| `FILENAME_CASE_SENSITIVE`, `FILE_EFFECTIVE_DATE_BASIS` | `true`, `RPT_START` | **Q-03**, **Q-04** |
| `SUPPORTED_FILE_TYPES` | `.txt,.csv` | **Q-02**. `.xlsx` / `.parquet` readers exist but are disabled by default. |
| `FILE_ENCODING`, `QUOTE_CHAR`, `EMPTY_AS_NULL` | `utf-8`, `"`, `true` | **Q-02** |
| `TRAILER_COUNT_CHECK`, `TRAILER_COUNT_REGEX` | `false`, `(\d+)` | **Q-02** |
| `XLSX_SHEET`, `XLSX_HEADER_ROW` | `0`, `0` | **Q-02** |
| `LOAD_ENGINE` | `PANDAS` | `SPARK` for large delimited files (needs `SPARK_JDBC_URL`). (Was `Engine_Cd`.) |
| `SPARK_JDBC_URL`, `SPARK_WRITE_PARTITIONS`, `SPARK_BATCH_SIZE` | —, `4`, `10000` | User and password come from the open connection. |
| `FILE_RULES_MODE` | `GATE` | `ANNOTATE` promotes with warnings. (Was `Rules_Vld_Md`.) File rules run when the source has FILE_LEVEL bindings. |
| `PERIOD_RULES_MODE` | `GATE` | (Was `Period_Rules_Vld_Md`.) |
| `RULE_ENGINE`, `GRE_ENTRYPOINT` | `gre`, — | **Q-12**. `none` disables rules; `module:Class` plugs in another engine. |
| `LOCK_TIMEOUT_SECONDS`, `HEARTBEAT_STALE_MINUTES` | `300`, `30` | |
| `ADHOC_ALLOW_ADD_SOURCE_BEFORE_TRIGGER`, `CYCLE_INIT_EXISTING_BATCH` | `true`, `SKIP` | **Q-05**, **Q-18** |
| `EXTRACT_GATING_MODE` | `STRICT_ALL_PASS` | or `BEST_EFFORT`. (Was `Extract_Gating_Md`.) |
| `EXTRACT_JOB_TYPE` | `GLUE_JOB` | or `HTTP_API` |
| `EXTRACT_JOB_NAME` | — | Glue job to start. |
| `EXTRACT_ENDPOINT_URL`, `EXTRACT_HTTP_METHOD`, `EXTRACT_AUTH_SECRET_NAME` | —, `POST`, — | HTTP API (**Q-08**: secret JSON `{"header_name", "header_value"}`). |
| `EXTRACT_CALL_TIMEOUT_SEC`, `EXTRACT_MAX_CALL_RETRIES` | `60`, `0` | **Q-07** |
| `EXTRACT_PARAMS` | `{}` | JSON object, see below. (Was `ComplianceExtractJobParam`.) |
| `STRICT_WAIVER_AUTO_TRIGGER` | `false` | **Q-06** |
| `AUTO_RETRIGGER_AFTER_REOPEN` | `true` | D-41 / **Q-16** |
| `RETRY_FAILED_TRIGGERS_ON_SWEEP`, `CALL_RETRY_BACKOFF_SECONDS`, `TRIGGER_RECONCILE_MINUTES` | `false`, `5`, `15` | **Q-07** |
| `HTTP_ACCEPTED_STATUS`, `PARAM_DATE_FORMAT` | `200-299`, `%Y-%m-%d` | |
| `NOTIFY_BACKEND`, `NOTIFY_FROM_EMAIL`, `DEFAULT_NOTIFY_EMAILS`, `SNS_TOPIC_ARN` | `log`, —, —, — | D-54 (`aws` = SES/SNS). (`SNS_TOPIC_ARN` was a file-config column.) |

**`EXTRACT_PARAMS`** maps each job argument / JSON field to a text template:

```json
{"--PERIOD_START": "{rpt_start_dt_key}", "--PERIOD_END": "{rpt_end_dt_key}",
 "--BATCHES": "{btch_id_list}", "--TRIGGER_ID": "{trigger_id}", "--MODE": "full"}
```

Placeholders: `extract_id, project_cd, table_nm, run_ty, rpt_start_dt_key, rpt_end_dt_key, extract_stat, extract_rules_stat, required_src_cnt, received_src_cnt, waived_src_cnt, included_src_cds, missing_src_cds, waived_src_cds, carried_src_cds, trigger_cnt, combine_run_cnt, btch_id_list, load_id_list, trigger_id`. Dates use `PARAM_DATE_FORMAT`. An unknown placeholder blocks the trigger with a `ConfigError`.

### Report periods

`create-batches --period <NAME>` picks a statement from `src/framework/period_sql.py`:
`SAME_DAY`, `PREV_DAY`, `PREV_N_DAYS` (`--lookback-days`), `PREV_WEEK_SAME_DAY` (`--lookback-weeks`), `PREV_CALENDAR_WEEK`, `CURRENT_CALENDAR_MONTH`, `PREV_CALENDAR_MONTH`, `ROLLING_1_MONTH`, `PREV_CALENDAR_QUARTER`, `PREV_CALENDAR_YEAR`.

A project with its own calendar ships a file and passes it with `--period-file`:

```python
# prja_periods.py
PERIOD_SQL = {
    "FISCAL_HALF": """
        SELECT make_date(extract(year FROM %(sched_dt)s::date)::int, 1, 1) AS rpt_start,
               make_date(extract(year FROM %(sched_dt)s::date)::int, 6, 30) AS rpt_end""",
}
```

`%(sched_dt)s` is the run date in `BUSINESS_TZ` (or the `--as-of` date).

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # set FRAMEWORK_DB_* for your PostgreSQL
framework init-db                 # schema (skipped if present) + event vocabulary
framework test-connection
framework show-config
framework validate-config

export TEST_DATABASE_URL=postgresql://postgres@localhost:5432/fwtest   # a scratch database
pytest                            # tests drop and recreate the metadata schema
```

Python 3.10+ and PostgreSQL 14+ with `btree_gist` (tested on 16). `TEST_METADATA_SCHEMA=fw_meta` runs the suite against a non-default schema. Without `TEST_DATABASE_URL` only the unit tests run.

### Windows without Docker

1. **Install Python 3.10+** from python.org and tick "Add python.exe to PATH".
2. **Install PostgreSQL 14+.**
   - With admin rights: the EDB installer (<https://www.postgresql.org/download/windows/>); it includes `btree_gist`.
   - Without admin rights: download the EDB **zip binaries**, unzip to e.g. `C:\pgsql`, then:
     ```powershell
     C:\pgsql\bin\initdb.exe -D C:\pgdata -U postgres -A trust -E UTF8
     C:\pgsql\bin\pg_ctl.exe -D C:\pgdata -l C:\pgdata\log.txt start
     C:\pgsql\bin\createdb.exe -U postgres fwtest
     C:\pgsql\bin\createdb.exe -U postgres framework
     ```
3. **Set up and test** (PowerShell):
   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   pip install -e ".[dev]"
   $env:TEST_DATABASE_URL = "postgresql://postgres@localhost:5432/fwtest"
   pytest
   ```
4. **Run the CLI:** `copy .env.example .env`, set `FRAMEWORK_DB_NAME=framework`, `FRAMEWORK_DB_USER=postgres`, remove the password line, then `framework init-db`.
5. **Stop the database** when done: `C:\pgsql\bin\pg_ctl.exe -D C:\pgdata stop`.

## Onboarding a project

1. **Create the target tables** in the framework database (own schemas):
   - Staging: business columns in file order, plus `btch_id`, `load_id`, `src_file_nm`, `stg_load_dtts`.
   - Core: business columns plus `btch_id`, `load_id`, `current_ind`, `load_dtts`, `end_dtts` (see the comment at the end of `schema.sql`).
2. **Insert configuration rows** (SQL or the Glue metadata-load job), in this order:
   1. `ComplianceSourceSystem`
   2. `ComplianceRunType` — `SLA_Days` ≥ 1; `Carry_Fwd_Ind = 1` if batches of this run type may reuse the previous batch's data after approval.
   3. `ComplianceDataSetSourceXwalk` — one effective-dated row per project / table / source / run type. Nothing else.
   4. `ComplianceSourceFileConfig` — one active row per project / table / source: template, aliases, file format, S3 paths, staging/core tables, notification recipients.
   5. `ComplianceRuleBinding` — FILE_LEVEL per source (optional) and PERIOD_LEVEL (`Src_Cd = '*'`).
3. **Run `framework validate-config`.** It must report no `ERROR` issues.
4. **Schedule the project's jobs** (EventBridge / Step Functions / Glue triggers), for example:

   ```bash
   # monthly, 06:00 on the 1st
   framework create-batches --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH
   # every 15 minutes
   framework evaluate-extracts --project PRJA \
     --set EXTRACT_JOB_NAME=prja_extract --set EXTRACT_GATING_MODE=STRICT_ALL_PASS \
     --set 'EXTRACT_PARAMS={"--PERIOD_START": "{rpt_start_dt_key}", "--BATCHES": "{btch_id_list}"}'
   # S3 event -> ingest-file; polling -> process-intake, process-decisions, notify
   ```

   A missed `create-batches` run is recreated with `--as-of <missed date>`: the period follows that date; `Btch_ID` carries the actual creation date.

Filename template example (literal text plus exactly one of each placeholder, separated by literals):

```
{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt
```

## Commands

Options go after the command. Every command accepts `--set NAME=VALUE` (repeatable), `--env-file`, `--as-of` and `--log-level`.

| Command | Purpose | Typical trigger |
|---|---|---|
| `init-db` | Schema (once) + event vocabulary | Deploy |
| `show-config` | Settings with value and source; database target (no password) | Ops |
| `test-connection` | Connect and check the schema; exit 1 if not initialised | Deploy / ops |
| `validate-config` | Configuration checks; exit 1 on errors, logs `CONFIG_VALIDATION_FAILED` | CI / before activating config |
| `create-batches --project --run-type --period [--table] [--period-file] [--lookback-days] [--lookback-weeks]` | Batches of one project / ROUTINE run type for the period of the run date | Project schedule |
| `process-intake` | CYCLE_INIT / ADHOC_REQUEST / CORRECTION_REQUEST | Poll |
| `ingest-file --bucket --key [--version-id]` | One inbound object end to end | S3 event |
| `process-decisions` | Act on approvals, rejections, waivers and carry-forwards; promote approved reopens; re-trigger (D-41) when the extract job is configured | Poll |
| `evaluate-extracts [--project] [--table] [--run-type]` | Reconcile triggers; refresh extracts past the SLA hold; auto-trigger AUTO-eligible ones | Project schedule |
| `refresh-extract --extract-id` | Recount / combine / evaluate one extract | Manual |
| `trigger-extract --extract-id --requested-by [--ack-warnings]` | Manual trigger (exit 2 if blocked) | Human |
| `resolve-trigger --trigger-id --outcome accepted/failed --actor [--job-run-ref]` | Resolve a trigger call whose outcome is unknown | Human |
| `notify` | Send pending notifications | Poll |
| `health` | Operational report (§15.3) | Ops |

**Exit codes:** 0 = ok, 1 = completed with problems, 2 = blocked or framework error.

## Approvals, waivers and carry-forward (manual SQL, D-12)

Use the templates in `src/framework/sql/approvals.sql`. Every statement must report **1 row**; `process-decisions` picks the decision up and writes the audit trail.

- **Reopen:** approving requires naming the reviewed `Load_ID` (§12.4).
- **Source / rule waiver:** request, approve, reject or revoke before the extract is triggered.
- **Carry-forward (D-70):** for an open batch with no data, when the run type has `Carry_Fwd_Ind = 1`. Request it (optionally naming `Reuse_Btch_ID`), then approve it. On approval the batch becomes `CARRIED_FORWARD` and the extract counts it as received, combining the reused batch's current core rows. A file that arrives later for the open batch replaces the carry-forward; revoking it before the trigger returns the batch to `PENDING`; a file after the close is a `LATE_ARRIVAL_REOPEN`.

## GRE integration contract (Q-12)

Set `GRE_ENTRYPOINT=package.module:function` to a function with this signature:

```python
def run_rules(conn, rule_group: str, rule_variant: str, run_params: dict) -> list[dict]:
    # one dict per executed rule: {"rule_ref": "R1", "passed": True, "detail": None}
    # raise on technical failure (treated as ERROR -> retry, never as a data failure)
```

`conn` is the framework database (metadata, staging and core). `run_params`:
- **FILE_LEVEL:** `btch_id`, `load_id`, `stg_schema_nm`, `stg_tblnm`, project / table / source / run type and report dates.
- **PERIOD_LEVEL:** `extract_id`, `btch_id_list`, `load_id_list`, `core_schema_nm`, `core_tblnm` and the extract grain. For a carried source the lists contain the reused batch.

The framework applies GATE/ANNOTATE itself (`FILE_RULES_MODE`, `PERIOD_RULES_MODE`).

## Implementation status and open items

**Implemented and tested:** every flow in design §7, the §8 decision tables, §9 templates, §10 promotion and combine, §11 eligibility, trigger, close, re-trigger and reconciliation, §12 locks and idempotency, carry-forward (D-70), the validator, notifications and health.

**Not yet verified:**
- **Spark engine** (`LOAD_ENGINE=SPARK`): written against PySpark 3.x but not run here; test on Glue/Spark before enabling.
- **AWS adapters** (S3 store, Glue connector, SES/SNS, Secrets Manager): written against boto3; unit tests cover the Glue connector with a fake client and the database secret with a fake loader.

**Waiting on decisions** (defaults are configurable; see design §16): Q-02 file types, encoding and trailer layout; Q-03 case sensitivity; Q-04 effective-date basis; Q-05 – Q-08 ad-hoc additions, waiver auto-trigger, call retries, HTTP auth; Q-11 PostgreSQL version; Q-12 GRE entry point; Q-13 – Q-15 orchestration, rollout, security; Q-16 – Q-18 auto re-trigger, alerting, cycle-init duplicates.

**Not built:** Glue / Step Functions wrappers for the framework commands, their Terraform, CI pipeline.
