# CMS Compliance Framework: Python Package

Implementation of [`design/cms-compliance-framework-design.md`](design/cms-compliance-framework-design.md) (v6). File-by-file detail: [`module-reference.md`](module-reference.md).

- **Project-agnostic:** projects, tables, sources and run types are rows in six reference / configuration tables. The code has no project-specific branches.
- **Filename-driven:** incoming files are recognised by the templates in `ComplianceSourceFileConfig`.
- **Job-level settings:** connections, runtime settings and the report period are **not** tables. They come from `.env` (local), AWS Secrets Manager (database credentials) and the arguments of each project's scheduled job.
- **The framework closes batches; it does not build or track the extract** (D-76). `close-batches` closes each batch with data after its SLA hold and `close-batch` lets a person close one without data; a separate process produces the extract from the batches and their current core rows.

## Layout

```
pyproject.toml            package metadata; `framework` console script
build_wheel.sh            builds the Glue wheel into ../code/wheels
run_workflow.sh           starts a project's Step Functions workflow by hand
framework/
  cli.py                  commands (one command = one service)
  app.py                  service wiring only; health() composes each service's own report (§15.3)
  settings.py             settings (job args > env > .env > default) and the database connection
  common.py               errors, clock, Btch_ID, Req_Stat values/transitions
  db.py                   connections, advisory locks
  config.py               configuration rows, filename templates, validator
  period_sql.py           report-period SQL by name
  modules.py              module dispatcher: a name (BATCH_CREATION, FILE_LOAD) selects the service
  batches.py              batch creation (scheduled + ad-hoc), CRC rows
  ingest.py               file pipeline (single object or a path sweep) + resolution decision tables
  load.py                 file reading, staging (pandas/COPY or Spark), core swap
  rules.py                FILE_RULES: rules on loaded files, after the core load
  overrides.py            REUSE decisions: apply and expire
  closing.py              batch close: SLA sweep and manual close
  audit.py                audit writer, event vocabulary, email notifications
  adapters.py             S3/local store, GRE rules engine, email (log / SES)
  sql/schema.sql          schema (source of truth, CREATE-only, no CHECK constraints)
  sql/approvals.sql       manual override templates (reuse / late arrival / correction)
```

## Configuration

### Where values come from

| What | Source (highest precedence first) |
|---|---|
| Any setting | `--set NAME=VALUE` job argument > `FRAMEWORK_<NAME>` environment variable > `.env` file > built-in default |
| Database | `FRAMEWORK_DB_DSN` / `FRAMEWORK_DB_HOST`, `_PORT`, `_NAME`, `_USER`, `_PASSWORD`, `_SSLMODE`, `_CONNECT_TIMEOUT` (explicit values) > the Secrets Manager secret named by `FRAMEWORK_DB_SECRET_NAME` (JSON `host, port, dbname, username, password[, sslmode]`) |
| `.env` location | `--env-file`, else `FRAMEWORK_ENV_FILE`, else `./.env` (optional) |

- **Locally (optional):** set `FRAMEWORK_DB_*` environment variables, or put them in a git-ignored `.env` file.
- **In AWS:** set only `FRAMEWORK_DB_SECRET_NAME` (the existing RDS secret the AWS deployment in `module/aws/compliance_frameworks/compliance_batch_framework/` points at: `host, port, username, password, dbname`); pass project settings as Glue job arguments (`--set ...`).
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
| `LOAD_DUPLICATE` | `false` | `yes` loads a file whose name was already loaded; otherwise it is rejected with `FILE_REJECTED_DUPLICATE`. |
| `ARCHIVE_FOLDER` / `ERROR_FOLDER` | `Archive` / `Error` | Subfolders of the inbound folder that processed and rejected files move to. (Replaces `QUARANTINE_URI`, the per-config archive path and `S3_Quarantine_Path`.) |
| `FILENAME_CASE_SENSITIVE`, `FILE_EFFECTIVE_DATE_BASIS` | `true`, `RPT_START` | **Q-03**, **Q-04** |
| `SUPPORTED_FILE_TYPES` | `.txt,.csv` | **Q-02**. `.xlsx` / `.parquet` readers exist but are disabled by default. |
| `FILE_ENCODING`, `QUOTE_CHAR`, `EMPTY_AS_NULL` | `utf-8`, `"`, `true` | **Q-02** |
| `TRAILER_COUNT_CHECK`, `TRAILER_COUNT_REGEX` | `false`, `(\d+)` | **Q-02** |
| `XLSX_SHEET`, `XLSX_HEADER_ROW` | `0`, `0` | **Q-02** |
| `LOAD_ENGINE` | `PANDAS` | `SPARK` for large delimited files (needs `SPARK_JDBC_URL`). (Was `Engine_Cd`.) |
| `SPARK_JDBC_URL`, `SPARK_WRITE_PARTITIONS`, `SPARK_BATCH_SIZE` | —, `4`, `10000` | User and password come from the open connection. |
| `FILE_RULES_MODE` | `GATE` | How the `FILE_RULES` step records a failed rule: `GATE` = `Rules_Stat` `FAILED`, `ANNOTATE` = `PASSED_WITH_WARNINGS`. Rules run after the core load and never block it. |
| `RULE_ENGINE`, `GRE_ENTRYPOINT` | `gre`, — | **Q-12**. `none` disables rules; `module:Class` plugs in another engine. |
| `LOCK_TIMEOUT_SECONDS`, `HEARTBEAT_STALE_MINUTES` | `300`, `30` | A load not updated for `HEARTBEAT_STALE_MINUTES` is reported by `health`. |
| `NOTIFY_BACKEND`, `NOTIFY_FROM_EMAIL`, `DEFAULT_NOTIFY_EMAILS` | `log`, —, — | D-54. All notifications are email: `ses` sends through SES, `log` only logs them. `DEFAULT_NOTIFY_EMAILS` receives events not tied to one file config. |

### Report periods

`run --module BATCH_CREATION --period <NAME>` picks a statement from `framework/period_sql.py`:
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

## Running the CLI locally (optional)

Only for inspecting a database by hand; AWS runs the same commands through the Glue runner job.

```bash
pip install .                                            # from src/, Python 3.9+
export FRAMEWORK_DB_DSN=postgresql://user@host:5432/db   # PostgreSQL 14+
framework test-connection
framework show-config
framework validate-config
```

## Onboarding a project

1. **Create the target tables** in the framework database (own schemas):
   - Staging: business columns in file order, plus `btch_id`, `load_id`, `src_file_nm`, `stg_load_dtts`.
   - Core: business columns plus `btch_id`, `load_id`, `current_ind`, `load_dtts`, `end_dtts`. Index `btch_id` on staging and `btch_id WHERE current_ind = 1` on core.
2. **Insert configuration rows** (SQL or the Glue metadata-load job), in this order:
   1. `ComplianceProject` — the project code and its description.
   2. `ComplianceSourceSystem` — `Src_ID`, name, type.
   3. `ComplianceRunType` — `SLA_Days` ≥ 1 (the hold is `Req_Dt_Key + SLA_Days − 1`); `Carry_Fwd_Ind = 1` if batches of this run type may reuse the previous batch's data after approval; code letters/digits only.
   4. `ComplianceDataSetSourceXwalk` — one effective-dated row per project / table / source / run type. Nothing else.
   5. `ComplianceSourceFileConfig` — one active row per project / table / source: filename template (project, table and source written literally, e.g. `PRJA_TBLX_S1_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt`; its extension is the file type), delimiter, header/trailer flags, inbound path (files then move to its `Archive/` or `Error/` subfolder), staging table, core schema (the core table is `Table_Nm`), email recipients.
   6. `ComplianceRuleBinding` — rules run by the `FILE_RULES` step on each file after it is loaded to core, bound at any level: `'*'` in `Table_Nm`, `Src_ID` or `Run_Ty` means all. Every matching binding runs (additive); a rule bound at two levels runs once. Optional: a file with no matching binding skips the rules.

      | Level | `Table_Nm` | `Src_ID` | `Run_Ty` |
      |---|---|---|---|
      | Project | `*` | `*` | `*` |
      | Project + table | `ODAG1` | `*` | `*` |
      | Table, one run type | `ODAG1` | `*` | `CMS` |
      | One source | `ODAG1` | `210` | `*` |
   The schema has no CHECK constraints: `validate-config` checks the values instead.
3. **Run `framework validate-config`.** It must report no `ERROR` issues.
4. **Schedule the project's steps.** In AWS this is an entry in the deployment's `projects` map
   (EventBridge rule -> the project's Step Functions workflow -> the Glue runner; see
   `module/aws/compliance_frameworks/compliance_batch_framework/README.md`). Each step is one module run
   for the project:

   ```bash
   # 06:00 on the 1st: routine batches AND this project's pending ad-hoc requests
   framework run --module BATCH_CREATION --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH
   # every 15 minutes: load waiting files, apply / expire overrides, email events
   framework run --module FILE_LOAD --project PRJA
   framework run --module OVERRIDE_DECISIONS --project PRJA
   framework run --module NOTIFY --project PRJA
   # hourly: close the batches past their SLA hold that have data or are in exception
   framework run --module BATCH_CLOSE --project PRJA
   # batches past the hold without data are listed as "waiting" (and in `health`); a person closes them:
   #   framework close-batch --btch-id <Btch_ID> --closed-by <user>
   # hourly, without --project: NOTIFY for events that belong to no project
   ```

   The extract is produced by a separate process from the batches and their current core rows.

   A missed `BATCH_CREATION` run is recreated with `--as-of <missed date>`: the period follows that date; `Btch_ID` carries the actual creation date.

Filename template example (project, table and source as literal text, plus exactly one of each
placeholder `{RUNTY}`, `{RPTSTART}`, `{RPTEND}`, `{TS}`, separated by literals; the extension is the file type):

```
PRJA_TBLX_S1_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt
```

## Commands

Options go after the command. Every command accepts `--set NAME=VALUE` (repeatable), `--env-file`, `--as-of` and `--log-level`.

| Command | Purpose | Typical trigger |
|---|---|---|
| `run --module NAME [module parameters]` | Run one module by name: `BATCH_CREATION`, `FILE_LOAD` (see below). One Glue job / Step Functions state can start any of them | Any schedule or event |
| `list-modules` | Module names and parameters (no database needed) | Ops |
| `show-config` | Settings with value and source; database target (no password) | Ops |
| `test-connection` | Connect and check the schema; exit 1 if not initialised | Deploy / ops |
| `validate-config` | Configuration checks; exit 1 on errors, logs `CONFIG_VALIDATION_FAILED` | CI / before activating config |
| `process-decisions` | Apply approved `REUSE` overrides and remove the ones that ran out | Poll |
| `close-batches [--project] [--table] [--run-type]` | SLA sweep: close every open batch past its hold that has data (`COMPLETED`) or is in exception (`COMPLETED_WITH_EXCEPTION`); list the ones without data as `waiting`; exit 1 if a batch was locked | Project schedule |
| `close-batch --btch-id --closed-by` | Close one batch past its hold, with or without data (no data → `DATA_NOT_PROVIDED`); exit 2 if still inside the hold or already closed | Human |
| `notify` | Email pending notifications | Poll |
| `health` | Operational report (§15.3) | Ops |

**Exit codes:** 0 = ok, 1 = completed with problems, 2 = blocked or framework error.

### Calling a module by name

`framework run --module <NAME>` identifies the module from its name (case-insensitive; `-` and `_` are interchangeable, so `file-load` = `FILE_LOAD`), checks that the parameters belong to it, and calls the service that owns the work. An unknown module, a missing required parameter or a parameter the module does not take is exit 2 and nothing runs. The output is `{"module": ..., "result": ...}`. Batch creation (routine and ad-hoc) and file loading have no other entry point - there is no separate `create-batches` / `process-intake` / `ingest-file` / `ingest-path` command; `run --module BATCH_CREATION` / `run --module FILE_LOAD` is it.

| Module | Does | Parameters |
|---|---|---|
| `BATCH_CREATION` | One project, both kinds, in one call: routine batches (when `--run-type`/`--period` name a ROUTINE run type) *and* that project's pending ad-hoc intake requests (always attempted, project-scoped; scoped further to the run type when it names an ADHOC one) | `--project` · `[--run-type] [--period] [--table] [--period-file] [--lookback-days] [--lookback-weeks]` |
| `FILE_LOAD` | Load inbound files: one object, one location, one project's locations, or every configured location. In a folder shared with other projects, `--project` takes only its own templates' files | `--bucket --key [--version-id]` · `--bucket --prefix` · `--project` · none |
| `OVERRIDE_DECISIONS` | Apply approved `REUSE` overrides, expire the ones that ran out (= `process-decisions`); an invalid override is reported once | `[--project]` |
| `BATCH_CLOSE` | Close batches past their SLA hold that have data or are in exception (= `close-batches`); exit 0 even when some wait or are locked | `[--project] [--table] [--run-type]` |
| `NOTIFY` | Email pending events (= `notify`); without `--project` also events of no project. Each email is claimed with `SKIP LOCKED`, so overlapping runs never send one twice | `[--project]` |

```bash
framework run --module BATCH_CREATION --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH
framework run --module BATCH_CREATION --project PRJA                       # ad-hoc sweep only, no routine run
framework run --module BATCH_CREATION --project PRJA --run-type ADHOC      # ad-hoc sweep scoped to that run type
framework run --module FILE_LOAD --bucket inbound --key prja/in/<file>
framework run --module FILE_LOAD --project PRJA                             # PRJA's inbound folders
framework run --module BATCH_CLOSE --project PRJA --run-type MONTHLY
```

`BATCH_CREATION` is the single, per-project trigger for both kinds of batch creation (Glue job
argument, EventBridge rule, cron line): it creates that project's routine batches and also picks up
anything sitting in `ComplianceRequestInTake` for it, without a second trigger and without touching
other projects' rows of either kind.

From Python: `app.run_module("FILE_LOAD", {"bucket": "inbound", "prefix": "prja/in/"})` returns a `ModuleOutcome(module, result, exit_code)`. To add a module, write one handler in `modules.py` that calls the owning service and register it in `MODULES` - the CLI, `list-modules` and parameter checks pick it up.

In Glue, keep one job definition and pass the module and its parameters as job arguments (`--module FILE_LOAD --bucket ... --prefix ...`), or one definition per module if you want separate schedules and IAM.

## Overrides (manual SQL, D-12, D-74)

Use the templates in `framework/sql/approvals.sql`. Every statement must report **1 row**. One shape covers three decisions, each approved with a `Valid_Thru_Dt_Key` — the last date it may be used. **There is no revoke:** template 6 moves that date into the past.

- **`REUSE` (D-70):** for an open batch with no data, when the run type has `Carry_Fwd_Ind = 1`. Optionally name `Reuse_Btch_ID`; otherwise the latest earlier closed batch with data is used. `process-decisions` applies it (the batch becomes `CARRIED_FORWARD` and counts as received, combining the reused batch's current core rows) and removes it again when it runs out. A file that arrives later for the open batch replaces the carried data.
- **`LATE_ARRIVAL`:** lets a file be promoted into a **closed** batch that has no data.
- **`CORRECTION`:** lets a file replace the data of a **closed** batch that has data.
- Both of the last two are read by the ingest pipeline: with a valid override the file is promoted (logged as `LATE_ARRIVAL_PROMOTED` / `CORRECTION_PROMOTED` on the batch timeline, which the extract process can watch); without one it is quarantined as `FILE_REJECTED_BATCH_CLOSED`, and re-delivering the same object after the approval reprocesses it.

## GRE integration contract (Q-12)

Set `GRE_ENTRYPOINT=package.module:function` to a function with this signature:

```python
def run_rules(conn, rule_group: str, rule_variant: str, run_params: dict) -> list[dict]:
    # one dict per executed rule: {"rule_ref": "R1", "passed": True, "detail": None}
    # raise on technical failure (treated as ERROR -> retry, never as a data failure)
```

`conn` is the framework database (metadata, staging and core). Rules run on each file after it is loaded to core (`run --module FILE_RULES`); `run_params`: `scope` (`FILE_LEVEL`), `btch_id`, `load_id`, `stg_schema_nm`, `stg_table_nm`, `core_schema_nm`, `core_table_nm`, `project_cd` / `table_nm` / `src_id` / `run_ty` and the report and run dates.

The framework applies GATE/ANNOTATE itself (`FILE_RULES_MODE`); neither blocks or undoes the load.

## Implementation status and open items

**Implemented and tested:** every flow in design §7, the §8 decision tables, §9 templates, §10 promotion, §11 batch close, §12 locks and idempotency, the three override types (D-74), the per-run-date grain (D-77), ad-hoc request windows (D-79), the validator, notifications, the `FILE_LOAD` multi-file/multi-config/multi-batch sweep, and health.

**Not yet verified:**
- **Spark engine** (`LOAD_ENGINE=SPARK`): written against PySpark 3.x but not run here; test on Glue/Spark before enabling.
- **AWS adapters** (S3 store, SES, Secrets Manager): written against boto3; the database secret is covered by a unit test with a fake loader.

**Waiting on decisions** (defaults are configurable; see design §16): Q-02 file types, encoding and trailer layout; Q-03 case sensitivity; Q-04 effective-date basis; Q-05 ad-hoc source additions; Q-11 PostgreSQL version; Q-12 GRE entry point; Q-13 – Q-15 orchestration, rollout, security; Q-17 alerting on batches that stay open.

**Not built:** Glue / Step Functions wrappers for the framework commands, their Terraform, CI pipeline.
