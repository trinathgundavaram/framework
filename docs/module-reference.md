# CMS Compliance Framework: Module Reference

What each file in `src/framework/` does, what it owns, and what it leaves to other modules. Matches package version 0.2.0 (design v4).

- **Design:** [`design/cms-compliance-framework-design.md`](design/cms-compliance-framework-design.md). `D-nn` = decision, `Q-nn` = open question, `§n` = design section.
- **Setup, configuration, commands:** [`framework-package.md`](framework-package.md).

## Contents

1. [Conventions](#1-conventions)
2. [Architecture at a glance](#2-architecture-at-a-glance)
3. [Command → module map](#3-command--module-map)
4. [Modules](#4-modules)
5. [SQL files](#5-sql-files)
6. [Tests](#6-tests)
7. [Who writes which table](#7-who-writes-which-table)
8. [What changed in v4](#8-what-changed-in-v4)

---

## 1. Conventions

| Term | Meaning |
|---|---|
| **Database** | One PostgreSQL database. The framework tables live in the schema named by `METADATA_SCHEMA`; staging and core tables live in their own schemas of the same database (named in `ComplianceSourceFileConfig`). |
| **CRC** | `ComplianceRequestControl`, one row per batch. |
| **Batch** | One (project, table, source, run type, report start, report end). Identified by `Btch_ID`. |
| **Load** | One physical file (`ComplianceFileLoad`, identified by `Load_ID`). |
| **Extract** | One (project, table, run type, report period) across all sources (`ComplianceExtractControl`). |
| **Job setting** | A value from `settings.py` (job argument > environment > `.env` > default). Replaces the removed settings, connection, period, policy and job-parameter tables. |

**Rules that apply to every module**
- **Time:** nothing reads the system clock directly. Time comes from `common.Clock`, so every command can run with `--as-of`.
- **Transactions:** connections are autocommit. Every unit of work is an explicit `with conn.transaction():` block. Because staging, core and control tables share one database, a promotion (core swap + CRC update + audit) commits or rolls back as one transaction.
- **Audit:** only `audit.EventLogger` writes the two audit tables. Audit text holds ids, codes and counts, never file content (no PHI).
- **Business outcome vs failure:** a rejected file, a failed rule or a blocked trigger is a *returned result*, recorded in the audit table. Only technical problems raise exceptions, so the caller can retry.
- **Project-agnostic:** no module contains project, table or source names.

---

## 2. Architecture at a glance

```
 cli.py ──► settings.py (job args > env > .env > defaults; DB from .env or Secrets Manager)
   │
   ▼
 app.py (App: one connection + services, health report)
   │
   ├─ batches.py    create-batches (period_sql.py), intake
   ├─ ingest.py     file pipeline + §8 decision tables ──► load.py (read, stage, swap)
   ├─ overrides.py  approvals, waivers, carry-forward, reopen promotion ──► load.py
   ├─ extract.py    eligibility, refresh/combine, trigger + close, SLA sweep
   └─ audit.py      EventLogger, NotificationDispatcher

 shared: common.py (errors, clock, Btch_ID, Req_Stat), config.py (config rows, templates, validator),
         db.py (schema install, advisory locks), adapters.py (S3/local store, GRE, Glue/HTTP, SES/SNS)
```

**Layering rule:** `common`, `settings`, `db`, `load`, `config`, `adapters` and `audit` never import a service module (`batches`, `ingest`, `overrides`, `extract`, `app`, `cli`).

---

## 3. Command → module map

| Command | Flow (§7) | Modules, in order |
|---|---|---|
| `init-db` | deploy | `cli` → `settings.connect` → `db.init_db` → `sql/schema.sql`, `sql/seed.sql` |
| `show-config` | ops | `cli` → `settings.describe` / `settings.db_conninfo` (no password) |
| `test-connection` | ops | `cli` → `settings.connect` → `db.schema_exists` |
| `validate-config` | P1 | `config.validate_all` → `load.columns` |
| `create-batches` | P2 | `batches.create_batches` → `batches.compute_period` (`period_sql.py` or `--period-file`) → `batches.create_batch` |
| `process-intake` | P4 | `batches.IntakeProcessor` → `batches.create_batch` |
| `ingest-file` | P5, P6 | `ingest.IngestPipeline` → `config.TemplateMatcher` → `adapters` (store) → `load.stage` → `adapters` (rules) → `ingest.decide` → `load.swap` → `extract.ExtractControlService.refresh` (early completion) |
| `process-decisions` | P7, P8 | `overrides.DecisionProcessor` → `load.restage` (if needed) → `load.swap` → `extract` refresh / re-trigger |
| `evaluate-extracts` | P10 | `extract.ExtractEvaluator` → `ExtractTriggerService.reconcile` / `fire` → `ExtractControlService` → `adapters` (Glue / HTTP) |
| `refresh-extract` | P9 | `extract.ExtractControlService.refresh` |
| `trigger-extract` | P11 | `extract.ExtractTriggerService.fire` (MANUAL) |
| `resolve-trigger` | P12 | `extract.ExtractTriggerService.resolve` |
| `notify` | P13 | `audit.NotificationDispatcher` → `adapters` (log / SES / SNS) |
| `health` | ops | `app.App.health` |

Exit codes: 0 = ok, 1 = completed with problems, 2 = blocked or framework error.

---

## 4. Modules

### `cli.py`
- Parses arguments (options go **after** the command), loads `Settings` with `--set NAME=VALUE` overrides and `--env-file`, builds the `App`, calls one service, prints JSON, sets the exit code.
- `init-db`, `show-config` and `test-connection` run without the `App` (the schema may not exist yet).
- `validate-config` writes one `CONFIG_VALIDATION_FAILED` audit event when errors are found.
- Scope arguments `--project / --table / --run-type` select what `create-batches` and `evaluate-extracts` work on, so one scheduled job per project carries that project's settings.

### `app.py`
- `App.from_settings`: opens the connection, checks the schema, builds the object store and rules engine.
- Creates each service on first use and wires the hooks: a promoted file refreshes its extract (`EARLY_COMPLETE`); a waiver / carry-forward decision refreshes its extract (`OVERRIDE_DECISION`); a promoted reopen calls `evaluator.after_reopen_promotion` (D-41).
- `health()`: stale loads, pending reviews, failed promotions, in-flight triggers, extracts past their hold, re-triggers required, quarantine counts (§15.3).

### `settings.py`
- `Settings`: every runtime and job-level setting with its default, and where each value came from (`argument`, `env`, `.env`, `default`).
- `Settings.load(env, overrides, env_file)`: layers job arguments > `FRAMEWORK_<NAME>` environment > `.env` file > default; converts text to the declared type; validates enumerations and the schema name.
- `db_conninfo()` / `connect()`: the database from `FRAMEWORK_DB_SECRET_NAME` (Secrets Manager JSON) and/or `FRAMEWORK_DB_DSN` / `FRAMEWORK_DB_*`; explicit values override the secret; `search_path` is set to the metadata schema. Passwords never appear in `describe` output.
- `read_env_file`: minimal `.env` reader (no extra dependency).
- **Replaces:** `ComplianceFrameworkSetting`, `ComplianceDbConnection`, `framework.ini`, `connections.py`.

### `common.py`
- Exception hierarchy (`ConfigError`, `LockTimeout`, `TechnicalFailure`, `FileRejected`, `Trigger*` ...).
- `Clock`, `FixedClock`, `parse_as_of`.
- `build_btch_id`, `earliest_close_date` (§4, D-38).
- `Req_Stat` values and `TRANSITIONS` / `check_transition` (§6.1). **Replaces** `ComplianceRequestStatus` and `ComplianceRequestStatusTransition`: the values are fixed by a CHECK constraint on CRC.

### `db.py`
- `connect(dsn, schema)` for tests and ad-hoc use; `init_db` creates the schema, installs `btree_gist`, applies `schema.sql` once and re-applies `seed.sql`.
- Advisory locks (§12.2): `held` (session lock with timeout), `try_lock` / `unlock`, `xact_lock`; key builders `extract_key`, `batch_key`, `seq_key`. Lock order is always EXT → BTCH.

### `config.py`
- Typed rows: `RunType` (incl. `carry_fwd`), `XwalkRow`, `FileConfig`, `RuleBinding`.
- Read access: `run_type(s)`, `xwalk_rows`, `effective_xwalk`, `effective_sources`, `active_file_configs`, `file_config` (per source, or any source of a table), `file_config_by_id`, `rule_bindings`.
- Filename templates (§9): `parse_template`, `compile_template`, `render`, `TemplateMatcher` (unique match or `MatchError` with the quarantine code).
- `validate_all` (P1): event vocabulary, crosswalk ↔ file config coverage, inactive run types (warning), template grammar and extension, S3 paths, staging/core tables and their framework columns, template overlap, alias collisions.
- **Out of scope:** schedules, periods, time zones, extract jobs — these are job settings now.

### `batches.py`
- CRC and extract rows: `find_batch`, `get_batch`, `batches_of_extract`, `find_extract`, `create_batch` (idempotent per period, D-30; no batch is added to a triggered extract).
- Report period: `period_sql(name, period_file)` returns the SQL for a name from `period_sql.py` or from a project file that defines `PERIOD_SQL`; `compute_period` runs it for the run date and checks lookback parameters.
- `create_batches(project, run type, period, [table], ...)` (P2): run date = today in `BUSINESS_TZ` (or `--as-of`); one batch per effective crosswalk row; `Required_Src_Cnt` per table. A missed run is recreated by running the job with `--as-of` (replaces cron expansion and catch-up).
- `IntakeProcessor` (P4): CYCLE_INIT, ADHOC_REQUEST (D-35, Q-05), CORRECTION_REQUEST; failures are recorded per intake.

### `period_sql.py`
- `PERIOD_SQL`: name → one `SELECT ... AS rpt_start, ... AS rpt_end` using `%(sched_dt)s`, `%(lookback_days)s`, `%(lookback_weeks)s`. **Replaces** `CompliancePeriodStrategy` and the `sql/period_strategies/*.sql` files.

### `ingest.py`
- `decide(ResolutionInput)` (§8): pure decision tables O-1 … O-4 and X-1 … X-5. A batch with an applied carry-forward counts as having data; a closed carried batch reopens as `LATE_ARRIVAL_REOPEN`.
- `IngestPipeline.process_file` (P5): registers the object (C0 idempotency), matches the template, checks location, run type, effective crosswalk and batch, stages the file, runs FILE_LEVEL rules **when bindings exist** (mode `FILE_RULES_MODE`), resolves, promotes in the same transaction, archives or quarantines, and handles replays and technical failures.
- When a real file replaces an applied carry-forward on an open batch, the carry-forward override is revoked by `SYSTEM` (`CARRY_FORWARD_REMOVED`).

### `load.py`
- Table introspection: `columns`, `staging_business_columns`, `core_insert_columns` (identity / generated / serial columns are skipped).
- File reading (§9.4 C12–C14): `read_file`, `read_delimited`, `scan_delimited` (streaming), `read_with_pandas` (xlsx / parquet), `sanitize_db_error`.
- `stage(conn, settings, ...)`: delete staging rows of the batch (D-05) and load the file; `LOAD_ENGINE=PANDAS` (reader + COPY) or `SPARK` (streaming scan + JDBC append; needs `SPARK_JDBC_URL`).
- `restage` (D-53) from the S3 archive with checksum check; `staged_row_count`; `swap` (D-01) disables current core rows of the batch and appends the load's rows, checking the row count.
- **Replaces:** `load_exclude_col_list` (removed), `Engine_Cd` (now `LOAD_ENGINE`).

### `overrides.py`
- `DecisionProcessor.run` (P7): detects `Apprvl_Stat <> Last_Processed_Apprvl_Stat`, validates the transition, audits, and acts:
  - reopen approved → `promote_reopen` (re-stage if needed, swap, CRC → `COMPLETED`, clears `Reuse_Btch_ID`);
  - waiver approved / rejected / revoked → extract refresh;
  - **carry-forward approved (D-70)** → checks the run type's `Carry_Fwd_Ind`, that the batch is open and has no promoted data, and that an earlier closed batch with data exists (or the requested `Reuse_Btch_ID`); sets CRC `Resolution_Ty='CARRY_FORWARD'`, `Req_Stat='CARRIED_FORWARD'`, `Reuse_Btch_ID` (always the batch that physically holds the data);
  - **carry-forward revoked** → CRC back to `PENDING`.
- Decisions on waivers / carry-forwards after the extract was triggered are invalid (D-48).

### `extract.py`
- `compute_eligibility` (§11.2): SLA hold, STRICT_ALL_PASS / BEST_EFFORT, waivers. Carried sources count as received.
- `ExtractControlService.refresh` (P9): recount (received = NEW_FILE + CARRY_FORWARD), `Carried_Src_Cds`, data signature over (Btch_ID, Load_ID) pairs — a carried batch contributes the reused batch's pair — combine + PERIOD_LEVEL rules (mode `PERIOD_RULES_MODE`), re-trigger flag, eligibility.
- `render_params(EXTRACT_PARAMS, ...)`: `{"--NAME": "text {placeholder}"}` with extract attributes, `{btch_id_list}`, `{load_id_list}`, `{trigger_id}`. **Replaces** `ComplianceExtractJobParam`.
- `ExtractTriggerService` (§11.3): `fire` (checks eligibility and the job config, locks batches, records the trigger with `Extract_Job_Ref`, calls the connector with retries, closes batches), `complete`, `fail`, `reconcile`, `resolve`. Close: `EXCEPTION_PENDING` → `COMPLETED_WITH_EXCEPTION`; NEW_FILE or CARRY_FORWARD → `COMPLETED`; otherwise `DATA_NOT_PROVIDED` / `MISSING`. **Replaces** `ComplianceExtractPolicy`.
- `ExtractEvaluator.run(project, table, run type)` (P10): sweeps extracts in the job's scope past the hold; `after_reopen_promotion` re-triggers only when the process has the extract job configured.

### `adapters.py`
- Object storage: `ObjectStore`, `LocalObjectStore` (`local://` and tests), `S3ObjectStore`, `parse_uri`, `basename`, `dirname`, `sha256_file`, `build_object_store`.
- Rules engine (Q-12): `RuleOutcome`, `CallableRuleEngine` (applies GATE / ANNOTATE), `NoRulesEngine`, `build_rule_engine` (`RULE_ENGINE=gre|none|module:Class`, `GRE_ENTRYPOINT`).
- Extract connectors (D-39, D-42): `GlueJobConnector` (`EXTRACT_JOB_NAME`), `HttpApiConnector` (`EXTRACT_ENDPOINT_URL`, `EXTRACT_HTTP_METHOD`, `EXTRACT_AUTH_SECRET_NAME`, `HTTP_ACCEPTED_STATUS`), `build_connector` (`EXTRACT_JOB_TYPE`).
- Notification channels (D-54): `LogChannel`, `AwsChannel` (SES + SNS `SNS_TOPIC_ARN`), `build_channel`.

### `audit.py`
- `EventLogger`: `batch_event` → `ComplianceRequestFileDetail`, `audit` → `CMS_ComplianceExceptionsAudit`; checks the event type and its target table.
- `NotificationDispatcher` (P13): unsent notifiable events; recipients from the file config (failure / success lists) or `DEFAULT_NOTIFY_EMAILS`; channel from the file config or the event type; marks `Notified_Ind`.

---

## 5. SQL files

| File | Purpose |
|---|---|
| `sql/schema.sql` | All framework tables, constraints, indexes and the `vw_crc_current_flags` view. Schema-unqualified; applied once by `init-db`. The Glue metadata job uploads this same file for reference. |
| `sql/seed.sql` | Event vocabulary (target table, category, severity, notify flag). Re-run by every `init-db`. |
| `sql/approvals.sql` | Manual templates: approve / reject reopens, request / approve / revoke source waivers, rule waivers and carry-forwards. Each statement must report 1 row. |

---

## 6. Tests

| File | Covers |
|---|---|
| `conftest.py` | `conn` fixture: recreates the metadata schema and the sample `stg_t` / `core_t` tables in `TEST_DATABASE_URL`. |
| `helpers.py` | Synthetic configuration seed, job-level settings (`make_app`), fake rules engine and connector, `create_batches`, file builders, approval helper. |
| `test_units.py` | No database: templates, readers, local store, eligibility, §8 tables, Btch_ID, Req_Stat transitions, settings precedence and `.env`, DB conninfo from DSN / secret, period SQL lookup, extract parameter rendering. |
| `test_batches.py` | Every period, lookback checks, project period file, create-batches (one batch per period, re-run by `--as-of`, effective windows, scope, triggered extracts), intake. |
| `test_ingest.py` | Promotion, replacement, quarantine reasons, structural failures, duplicates, zero records, GATE / ANNOTATE, no bindings, technical failure and replay, rollback of a failed promotion. |
| `test_overrides.py` | Reopens (late arrival, correction, X-2 … X-5), rejection, archive re-staging, promotion failure, invalid manual changes; carry-forward approve / invalid / replaced by a file / revoked / late arrival after a carried close. |
| `test_extract.py` | Auto trigger and close, STRICT with waivers, BEST_EFFORT, call failure and retries, unknown outcomes, locked batches, period-rule errors, sweep scope and missing job config. |
| `test_config_cli.py` | Validator, notifications, CLI end to end (with `.env` and `--set`), rules adapter contract, HTTP and Glue connectors. |

---

## 7. Who writes which table

| Table | Written by |
|---|---|
| `ComplianceRequestControl` | `batches.create_batch` (create), `ingest` (status, current load), `overrides` (carry-forward, reopen promotion), `extract` (close) |
| `ComplianceExtractControl` | `batches` (create, hold date, required count), `extract` (counts, rules, eligibility, trigger status, close) |
| `ComplianceExtractTrigger` | `extract` |
| `ComplianceFileLoad` | `ingest`, `overrides` (promoted, superseded) |
| `ComplianceBatchOverride` | `ingest` (reopen rows, carry-forward revoked by a file), `overrides` (processing state, promotion); people via `sql/approvals.sql` |
| `ComplianceRequestInTake` | people / upstream systems (new rows); `batches.IntakeProcessor` (result) |
| `ComplianceRequestFileDetail`, `CMS_ComplianceExceptionsAudit` | `audit.EventLogger` only (`NotificationDispatcher` sets `Notified_Ind`) |
| `ComplianceEventType` | `db.init_db` (`seed.sql`) |
| `ComplianceSourceSystem`, `ComplianceRunType`, `ComplianceDataSetSourceXwalk`, `ComplianceSourceFileConfig`, `ComplianceRuleBinding` | people via SQL or the Glue metadata-load job |
| Staging tables | `load.stage` (delete by `Btch_ID` + load) |
| Core tables | `load.swap` only |

---

## 8. What changed in v4

| Removed | Now |
|---|---|
| `ComplianceRequestStatus`, `ComplianceRequestStatusTransition` | Fixed `Req_Stat` CHECK list on CRC; transitions in `common.TRANSITIONS` |
| `CompliancePeriodStrategy`, `sql/period_strategies/*.sql` | `period_sql.py`; name passed as `create-batches --period` (or a project `--period-file`) |
| `ComplianceDbConnection`, `connections.py`, `framework.ini` | `.env` locally, `FRAMEWORK_DB_SECRET_NAME` in AWS; one database |
| `ComplianceFrameworkSetting` | Environment / `.env` / `--set` job arguments |
| `ComplianceExtractPolicy`, `ComplianceExtractJobParam` | `EXTRACT_*` job settings, `EXTRACT_PARAMS` JSON |
| Xwalk `Period_Strategy_Cd`, `Lookback_Days/Weeks`, `Schedule_Cron_Expr`, `Business_Tz` | `--period`, `--lookback-*`, the external schedule, `BUSINESS_TZ` |
| File config `Target_Connection_Nm`, `Engine_Cd`, `Rules_Vld_Md`, `Is_Rules_Engine_Required`, `Load_Exclude_Col_List`, `Sns_Topic_Arn` | Same database; `LOAD_ENGINE`; `FILE_RULES_MODE`; rules run when bindings exist; auto-skip of identity columns; `SNS_TOPIC_ARN` |
| `catchup` command, cron expansion, `croniter` | Re-run `create-batches --as-of <date>` |
| 55 Python files in 13 sub-packages (4,673 lines), 15 SQL files | 15 modules (3,744 lines), 3 SQL files |

| Added | Purpose |
|---|---|
| `ComplianceRunType.Carry_Fwd_Ind` | Run types whose batches may reuse the previous batch's data after approval (D-70) |
| Override type `CARRY_FORWARD`, `Reuse_Btch_ID` (override and CRC), `Req_Stat='CARRIED_FORWARD'`, `Resolution_Ty='CARRY_FORWARD'` | The approval flow for reuse |
| `ComplianceExtractControl.Carried_Src_Cds`, `ComplianceExtractTrigger.Extract_Job_Ref` | Visibility of carried sources and of the job that was called |
