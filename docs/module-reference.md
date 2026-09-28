# CMS Compliance Framework: Module Reference

What each file in `src/framework/` does, what it owns, and what it leaves to other modules. Matches package version 0.3.0 (design v5).

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
8. [What changed in v5](#8-what-changed-in-v5)

---

## 1. Conventions

| Term | Meaning |
|---|---|
| **Database** | One PostgreSQL database. The framework tables live in the schema named by `METADATA_SCHEMA`; staging and core tables live in their own schemas of the same database (named in `ComplianceSourceFileConfig`). |
| **CRC** | `ComplianceRequestControl`, one row per batch. |
| **Batch** | One (project, table, source, run type, report start, report end, **run date**) — `Req_Dt_Key` is part of the grain (D-77). Identified by `Btch_ID`. |
| **Load** | One physical file (`ComplianceFileLoad`, identified by `Load_ID`). A batch's current data is its single `PROMOTED` load (D-75). |
| **Extract** | Produced by a separate process from the batches and their current core rows; not tracked by the framework (D-76). |
| **Job setting** | A value from `settings.py` (job argument > environment > `.env` > default). Replaces the removed settings, connection, period, policy and job-parameter tables. |

**Rules that apply to every module**
- **Time:** nothing reads the system clock directly. Time comes from `common.Clock`, so every command can run with `--as-of`.
- **Transactions:** connections are autocommit. Every unit of work is an explicit `with conn.transaction():` block. Because staging, core and control tables share one database, a promotion (core swap + CRC update + audit) commits or rolls back as one transaction.
- **Audit:** only `audit.EventLogger` writes the two audit tables. Audit text holds ids, codes and counts, never file content (no PHI).
- **Business outcome vs failure:** a rejected file, a failed rule or a blocked close is a *returned result*, recorded in the audit table. Only technical problems raise exceptions, so the caller can retry.
- **Project-agnostic:** no module contains project, table or source names.
- **The framework does not generate or track the extract** (D-76). It closes each batch; a separate process reads the batches and builds the submission.

---

## 2. Architecture at a glance

```
 cli.py ──► settings.py (job args > env > .env > defaults; DB from .env or Secrets Manager)
   │
   ▼
 app.py (App: one connection + services; wiring only)
   │
   ├─ modules.py    module dispatcher: name -> BATCH_CREATION / FILE_LOAD
   ├─ batches.py    batch creation (period_sql.py), ad-hoc intake windows
   ├─ ingest.py     file pipeline (one object or a path sweep) + §8 decision tables ──► load.py (read, stage, swap)
   ├─ overrides.py  REUSE decisions: apply and expire
   ├─ closing.py    batch close: SLA sweep and manual close
   └─ audit.py      EventLogger, NotificationDispatcher

 shared: common.py (errors, clock, Btch_ID, Req_Stat), config.py (config rows, templates, validator),
         db.py (schema install, advisory locks), adapters.py (S3/local store, GRE, email)
```

**Layering rule:** `common`, `settings`, `db`, `load`, `config`, `adapters` and `audit` never import a service module (`batches`, `ingest`, `overrides`, `closing`, `app`, `cli`).

**Health is an instance of the same rule.** `App.health()` (§15.3) carries no table-specific SQL; it only composes the dict returned by each service that owns the tables in question: `IngestPipeline.health()` (`ComplianceFileLoad`), `DecisionProcessor.health()` (`ComplianceBatchOverride`) and `BatchCloser.health()` (batch close). Apply the same split to future operational reports or cross-cutting reads: the generic composition goes in `app.py`; the query that knows a table's columns and status values goes in the module that already owns that table (§7).

---

## 3. Command → module map

| Command | Flow (§7) | Modules, in order |
|---|---|---|
| `run --module NAME` | any | `cli` → `modules.run_module` → the module's service (rows below) |
| `run --module BATCH_CREATION` | P2, P4 | `modules` → `App.create_batches` for the project/run type/period when a ROUTINE run type is given, and always `batches.IntakeProcessor.run(project_cd=..., run_ty=...)` for that project - one project-scoped call for both routine and ad-hoc batch creation |
| `run --module FILE_LOAD` | P5, P6 | `modules` → `ingest.IngestPipeline.process_file` (`--key`) or `process_path` (`--prefix`, or every configured location) |
| `list-modules` | ops | `cli` → `modules.describe_modules` (no settings, no database) |
| `init-db` | deploy | `cli` → `settings.connect` → `db.init_db` → `sql/schema.sql` |
| `show-config` | ops | `cli` → `settings.describe` / `settings.db_conninfo` (no password) |
| `test-connection` | ops | `cli` → `settings.connect` → `db.schema_exists` |
| `validate-config` | P1 | `config.validate_all` → `load.columns` |
| `process-decisions` | P7 | `overrides.DecisionProcessor` |
| `close-batches` | P10 | `closing.BatchCloser.run` (SLA sweep) |
| `close-batch` | P11 | `closing.BatchCloser.close` |
| `notify` | P13 | `audit.NotificationDispatcher` → `adapters` (email: log / SES) |
| `health` | ops | `app.App.health` → `ingest.IngestPipeline.health` + `overrides.DecisionProcessor.health` + `closing.BatchCloser.health` |

Exit codes: 0 = ok, 1 = completed with problems, 2 = blocked or framework error.

---

## 4. Modules

### `cli.py`
- Parses arguments (options go **after** the command), loads `Settings` with `--set NAME=VALUE` overrides and `--env-file`, builds the `App`, calls one service, prints JSON, sets the exit code.
- `init-db`, `show-config` and `test-connection` run without the `App` (the schema may not exist yet).
- `validate-config` writes one `CONFIG_VALIDATION_FAILED` audit event when errors are found.
- Scope arguments `--project / --table / --run-type` select what `run --module BATCH_CREATION` and `close-batches` work on, so one scheduled job per project carries that project's settings.

### `app.py`
- `App.from_settings`: opens the connection, checks the schema, builds the object store and rules engine.
- Creates each service on first use (`closer`, `pipeline`, `decisions`, `intake`); there are no cross-service hooks.
- `health()`: merges the dict returned by `pipeline.health()`, `decisions.health()` and `closer.health()` — stale loads and quarantine counts by reason (ingest), pending reviews and overrides expiring within 7 days (overrides), open batches past their SLA hold (closing) (§15.3). `app.py` itself carries none of that SQL: each service reports on the tables it owns (§7), the same split the layering rule already applies to imports.

### `settings.py`
- `Settings`: every runtime and job-level setting with its default, and where each value came from (`argument`, `env`, `.env`, `default`).
- `Settings.load(env, overrides, env_file)`: layers job arguments > `FRAMEWORK_<NAME>` environment > `.env` file > default; converts text to the declared type; validates enumerations and the schema name.
- `db_conninfo()` / `connect()`: the database from `FRAMEWORK_DB_SECRET_NAME` (Secrets Manager JSON) and/or `FRAMEWORK_DB_DSN` / `FRAMEWORK_DB_*`; explicit values override the secret; `search_path` is set to the metadata schema. Passwords never appear in `describe` output.
- There are no close or extract settings: `SLA_Days` of the run type sets each batch's hold (D-76).
- **Replaces:** `ComplianceFrameworkSetting`, `ComplianceDbConnection`, `framework.ini`.

### `common.py`
- Exception hierarchy (`ConfigError`, `LockTimeout`, `TechnicalFailure`, `FileRejected`, `CloseBlocked`, `CloseDeferred`, ...).
- `Clock`, `FixedClock`, `parse_as_of`.
- `build_btch_id`, `earliest_close_date(req_dt, sla_days)` — computed, never stored (D-77).
- `Req_Stat` values and `TRANSITIONS` / `check_transition` (§6.1). **Replaces** `ComplianceRequestStatus` and `ComplianceRequestStatusTransition`.

### `db.py`
- `connect(dsn, schema)` for tests and ad-hoc use; `init_db` creates the schema and applies `schema.sql` once (no extensions, no seed data); `fetch_all` for the health queries.
- Advisory locks (§12.2): `held` (session lock with timeout), `try_lock` / `unlock`, `xact_lock`; key builders `batch_key`, `seq_key`.

### `config.py`
- Typed rows: `RunType` (incl. `sla_days`, `carry_fwd`), `XwalkRow`, `FileConfig` (`src_file_ty` is the template's extension; the core table is `Core_Schema_Nm.Table_Nm`), `RuleBinding`.
- Read access: `run_type(s)`, `xwalk_rows`, `effective_xwalk`, `effective_sources`, `active_file_configs`, `file_config`, `file_config_by_id`, `rule_bindings(project, table, source, run type, scope)` — every active binding whose `Table_Nm`, `Src_ID` and `Run_Ty` equal the given value or `'*'` (additive; a group/variant bound at several levels is returned once).
- Filename templates (§9): project, table and source are literal text; `{RUNTY}`, `{RPTSTART}`, `{RPTEND}`, `{TS}` once each. `parse_template`, `compile_template`, `render`, `TemplateMatcher` (unique match or `MatchError` with the quarantine code).
- `validate_all` (P1): the value rules the schema no longer enforces (run type code, category and `SLA_Days`; overlapping crosswalk windows; rule bindings that match no crosswalk row), crosswalk ↔ file config coverage, inactive run types and projects (warnings), template grammar and extension, S3 paths, staging/core tables and their framework columns, template overlap.
- **Out of scope:** schedules, periods, time zones — these are job settings now.

### `modules.py`
- The dispatcher behind `framework run --module NAME`: `MODULES` (name → `ModuleSpec` with required/optional `Param`s and a handler), `resolve_module` (case-insensitive, `-`/`_` interchangeable - no other name variants), `run_module(app, name, params)` → `ModuleOutcome(module, result, exit_code)`, `describe_modules`.
- Validates before anything runs: unknown module, missing required parameter, parameter the module does not take, wrong type (`ConfigError`, exit 2). Handlers only map a name to the service that owns the work (`batches`, `ingest`) - no SQL, no business rules; a new module is one handler plus one `MODULES` entry.
- `BATCH_CREATION` (the merged module, `BatchCreationSummary(scheduled, adhoc)`) → `App.create_batches` when `--run-type`/`--period` name a ROUTINE run type (`scheduled`, else `None`), and always `IntakeProcessor.run(project_cd=..., run_ty=...)` for that project (`adhoc`) - one call covers both routine and ad-hoc batch creation for a project, so a project needs only one trigger. `FILE_LOAD` → `process_file` (`--key`) or `process_path` (`--prefix`, or every configured location).

### `batches.py`
- CRC rows: `find_batch` (per report period **and run date**), `get_batch`, `promoted_load` (the batch's current data, D-75), `create_batch` (idempotent per run date).
- Report period: `period_sql(name, period_file)` returns the SQL for a name from `period_sql.py` or from a project file that defines `PERIOD_SQL`; `compute_period` runs it for the run date and checks lookback parameters.
- `create_batches(project, run type, period, [table], ...)` (P2): run date = today in `BUSINESS_TZ` (or `--as-of`); one batch per effective crosswalk row per run date; `Required_Src_Cnt` per table.
- `IntakeProcessor` (P4, D-79): ADHOC only; each request whose `[Req_Start_Dt_Key, Req_End_Dt_Key]` window contains the run date is handled once for that date (`Last_Run_Dt_Key`) and its batches are created. There is no status column: a request is due while its window is open, and outcomes are in the audit tables (`BATCH_CREATED` with the `Intake_ID`, `INTAKE_FAILED` with the reason, repeated on each run date until the configuration is fixed).

### `period_sql.py`
- `PERIOD_SQL`: name → one `SELECT ... AS rpt_start, ... AS rpt_end` using `%(sched_dt)s`, `%(lookback_days)s`, `%(lookback_weeks)s`. **Replaces** `CompliancePeriodStrategy`.

### `ingest.py`
- `decide(ResolutionInput)` (§8): pure decision tables O-1 … O-5 (open batch) and C-1 … C-4 (closed batch); `required_override_ty(has_data)` says which override type a closed batch needs.
- Batch selection (D-78): the open batch of the grain with the latest run date ≤ today; otherwise the most recent closed batch, which needs an approved, still-valid override. Without one the file is quarantined as `FILE_REJECTED_BATCH_CLOSED` — a **retryable** quarantine, so re-delivering the object after the approval reprocesses the same `Load_ID`.
- `IngestPipeline.process_file` (P5): registers the object (C0 idempotency), matches the template, checks location, run type, effective crosswalk and batch, stages the file, runs FILE_LEVEL rules **when bindings exist** (mode `FILE_RULES_MODE`), resolves, promotes in the same transaction (superseding the previous `PROMOTED` load first, D-75), archives or quarantines, and handles replays and technical failures.
- `IngestPipeline.process_path(bucket=None, prefix=None)`: lists objects at one location (`adapters.ObjectStore.list_objects`), or — with neither argument — at the distinct inbound location of every active file config (`_configured_locations`, de-duplicated, since several source configs commonly share one folder), and calls `process_file` once per object found. Each object still resolves to exactly one config and exactly one batch (D-26, D-33), under that batch's own lock, exactly as a separate `run --module FILE_LOAD --key ...` call would; a listing problem or a per-object technical failure is recorded on the returned `PathIngestSummary` and does not stop the rest of the sweep.
- A file promoted into an open carried-forward batch clears `Reuse_Btch_ID` and logs `CARRY_FORWARD_REMOVED`; a promotion into a closed batch is logged as `LATE_ARRIVAL_PROMOTED` / `CORRECTION_PROMOTED`.
- `health()`: stale loads and quarantine counts by reason — `ingest.py` owns `ComplianceFileLoad` (§7), so its health queries live here rather than in `app.py`.

### `load.py`
- Table introspection: `columns`, `staging_business_columns`, `core_insert_columns` (identity / generated / serial columns are skipped).
- File reading (§9.4 C12–C14): `read_file`, `read_delimited`, `scan_delimited` (streaming), `read_with_pandas` (xlsx / parquet), `sanitize_db_error`.
- `stage(conn, settings, ...)`: delete staging rows of the batch (D-05) and load the file; `LOAD_ENGINE=PANDAS` (reader + COPY) or `SPARK` (streaming scan + JDBC append; needs `SPARK_JDBC_URL`).
- `swap` (D-01) disables the current core rows of the batch and appends the load's rows, checking the row count.

### `overrides.py`
- `DecisionProcessor.run` (P7, D-74): handles `REUSE` only — `LATE_ARRIVAL` and `CORRECTION` are read by the ingest pipeline.
  - **apply:** an approved, still-valid `REUSE` on an open batch without data → checks `Carry_Fwd_Ind`, that the batch has no promoted load, and that a source batch exists (the requested `Reuse_Btch_ID`, else the latest earlier closed batch with data; a carried batch resolves to its own source). Sets CRC `Resolution_Ty='CARRY_FORWARD'`, `Reuse_Btch_ID`, `Req_Stat='CARRIED_FORWARD'`; logs `OVERRIDE_APPROVED` + `CARRY_FORWARD_APPLIED`. An invalid row → `OVERRIDE_INVALID_DETECTED`, batch unchanged.
  - **expire:** an open carried batch whose override ran out (`Valid_Thru_Dt_Key < today`), was rejected or is gone → back to `PENDING`, `OVERRIDE_EXPIRED` + `CARRY_FORWARD_REMOVED`.
- There is no revoke and no promotion state: stopping an override is a date change (`sql/approvals.sql` template 6).
- `health()`: pending reviews and approved overrides expiring within 7 days, across all three override types — `overrides.py` owns `ComplianceBatchOverride` (§7), so its health queries live here rather than in `app.py`.

### `closing.py`
- Each batch closes on its own after its hold `Req_Dt_Key + (SLA_Days − 1)` (D-38, D-77); the extract is produced by a separate process (D-76).
- `close_resolution(batch)`: `EXCEPTION_PENDING` → `COMPLETED_WITH_EXCEPTION`; `NEW_FILE` / `CARRY_FORWARD` → `COMPLETED`; otherwise `DATA_NOT_PROVIDED` / `MISSING` (+ `SOURCE_MISSING_AT_CLOSE`). `closes_automatically(batch)`: has data or is in exception.
- `BatchCloser.run(project, table, run type)` (P10, `close-batches`): sweeps the open batches in scope whose hold has passed; closes the ones that close automatically; lists the others as `waiting` (a person decides) and the locked ones as `deferred` (next sweep, `BATCH_CLOSE_DEFERRED_LOCKED`).
- `BatchCloser.close(btch_id, closed_by)` (P11, `close-batch`): closes one open batch past its hold, whatever its data; `CloseBlocked` + `BATCH_CLOSE_BLOCKED` inside the hold or when already closed.
- Every close takes the batch lock, re-reads the row `FOR UPDATE`, checks the `Req_Stat` transition and logs `BATCH_CLOSED` with the actor.
- `health()`: open batches past their SLA hold.

### `adapters.py`
- Object storage: `ObjectStore`, `LocalObjectStore` (`local://` and tests), `S3ObjectStore`, `parse_uri`, `basename`, `dirname`, `sha256_file`, `build_object_store`.
- `ObjectStore.list_objects(bucket, prefix)`: every object directly under a location, non-recursive (Q-03: inbound files sit at the template's root, no sub-folders) — `LocalObjectStore` walks the directory, `S3ObjectStore` paginates `list_objects_v2` with `Delimiter="/"`. Backs `ingest.IngestPipeline.process_path`.
- Rules engine (Q-12): `RuleOutcome`, `CallableRuleEngine` (applies GATE / ANNOTATE), `NoRulesEngine`, `build_rule_engine` (`RULE_ENGINE=gre|none|module:Class`, `GRE_ENTRYPOINT`).
- Email channels (D-54): `LogChannel`, `SesChannel` (`NOTIFY_BACKEND=ses`, needs `NOTIFY_FROM_EMAIL`), `build_channel`.
- There are no extract connectors (D-76).

### `audit.py`
- Event vocabulary (replaces the `ComplianceEventType` table): `BATCH_EVENTS` (batch timeline) and `AUDIT_EVENTS` (event → category, default severity, emailed or not).
- `EventLogger`: `batch_event` → `ComplianceRequestFileDetail`, `audit` → `CMS_ComplianceExceptionsAudit`; rejects an event code outside its vocabulary. Events that are not emailed are written with `Notified_Ind = 1`.
- `NotificationDispatcher` (P13): emails every `Notified_Ind = 0` row; recipients from the file config (failure list for EXCEPTION events, success list otherwise) or `DEFAULT_NOTIFY_EMAILS`; marks `Notified_Ind`.

---

## 5. SQL files

| File | Purpose |
|---|---|
| `sql/schema.sql` | All 12 framework tables with named keys, foreign keys and the few indexes the framework needs; no CHECK constraints. Schema-unqualified, `CREATE`-only; applied once by `init-db`. |
| `sql/approvals.sql` | The six manual override templates (insert an approved `REUSE` / `LATE_ARRIVAL` / `CORRECTION`, approve, reject, stop early). Each statement must report 1 row. |

---

## 6. Tests

| File | Covers |
|---|---|
| `conftest.py` | `conn` fixture: recreates the metadata schema and the sample `stg_t` / `core_t` tables in `TEST_DATABASE_URL`. |
| `helpers.py` | Synthetic configuration seed, job-level settings (`make_app`), fake rules engine, `create_batches`, file builders, `add_override` / `stop_override`. |
| `test_units.py` | No database: templates, readers, local store (incl. `list_objects`), batch close rules, §8 tables and `required_override_ty`, `PathIngestSummary.tally`, Btch_ID, Req_Stat transitions, settings precedence and `.env`, DB conninfo from DSN / secret, period SQL lookup. |
| `test_batches.py` | Every period, lookback checks, project period file, create-batches (one batch per run date, daily runs of one period, re-run by `--as-of`, effective windows, scope, closed runs skipped), the removed CRC columns, ad-hoc intake windows (once per run date), the DDL standard (no CHECK constraints, named constraints, no event-type table). |
| `test_ingest.py` | Promotion, replacement, quarantine reasons, structural failures, duplicates, zero records, GATE / ANNOTATE, no bindings, technical failure and replay, closed-batch overrides (late arrival, correction, retry after approval), batch selection by run date, `process_path` (one location, every configured location, mixed outcomes, listing/technical errors that don't stop the sweep). |
| `test_overrides.py` | `REUSE`: apply, invalid cases, pending until approved, expiry, replacement by a real file, reuse chains; `LATE_ARRIVAL` / `CORRECTION` rows are left to the pipeline. |
| `test_closing.py` | SLA sweep (closes batches with data or in exception, leaves the rest waiting, idempotent), manual close of a batch without data, blocked inside the hold / when already closed, deferred on a locked batch, scope and per-run-date holds, health. |
| `test_modules.py` | Module identification (names, no aliases, unknown), parameter checks before anything runs, each module end to end (`BATCH_CREATION` routine + ad-hoc together, ad-hoc-only, ADHOC-run-type-scoped, per-project scoping for both halves; file load one object / one location / every location), `run --module` and `list-modules` on the CLI. |
| `test_config_cli.py` | Validator, notifications, CLI end to end (with `.env`, `--set`, `run --module`, `close-batches` and `close-batch`), the `FILE_LOAD` path sweep end to end, the composed `health` report, rules adapter contract. |

---

## 7. Who writes which table

| Table | Written by |
|---|---|
| `ComplianceRequestControl` | `batches.create_batch` (create), `ingest` (status, resolution), `overrides` (carry-forward apply / expire), `closing` (close) |
| `ComplianceFileLoad` | `ingest` (received, promoted, superseded, quarantined) |
| `ComplianceBatchOverride` | people via `sql/approvals.sql`; read by `ingest` and `overrides` |
| `ComplianceRequestInTake` | people / upstream systems (new rows); `batches.IntakeProcessor` (`Last_Run_Dt_Key`) |
| `ComplianceRequestFileDetail`, `CMS_ComplianceExceptionsAudit` | `audit.EventLogger` only (`NotificationDispatcher` sets `Notified_Ind`) |
| `ComplianceProject`, `ComplianceSourceSystem`, `ComplianceRunType`, `ComplianceDataSetSourceXwalk`, `ComplianceSourceFileConfig`, `ComplianceRuleBinding` | people via SQL or the Glue metadata-load job |
| Staging tables | `load.stage` (delete by `Btch_ID` + load) |
| Core tables | `load.swap` only |

---

## 8. What changed in v5

| Removed | Now |
|---|---|
| `ComplianceExtractTrigger`, the Glue / HTTP connectors, every `EXTRACT_JOB_*` / `EXTRACT_PARAMS` setting, `trigger-extract`, `resolve-trigger` | `close-extract` and the automatic close in `evaluate-extracts`; the project's job chain generates the extract from `Combine_Btch_ID_List` (D-76) |
| CRC `Earliest_Close_Dt`, extract `Earliest_Trigger_Dt` | Computed: `Req_Dt_Key + (SLA_Days − 1)` (D-77) |
| CRC `Current_Load_ID` | The batch's single `PROMOTED` load (`ux_fileload_promoted`, D-75) |
| CRC `Intake_ID`, `Closed_By_Trigger_ID` | `Created_By` (`SCHEDULER` / `ADHOC_INTAKE`); the close is recorded on the extract row (D-79) |
| Override types `SOURCE_WAIVER`, `RULE_WAIVER`, `*_REOPEN`; `REVOKED`; candidate / reviewed loads; `Promotion_Stat`; `Last_Processed_Apprvl_Stat` | `REUSE` / `LATE_ARRIVAL` / `CORRECTION` with `Valid_Thru_Dt_Key`; no revoke, no promotion job (D-74) |
| `load.restage` and the archive re-stage path (D-53) | A file is staged and promoted in one run; a closed-run quarantine is retried by re-delivering the object (D-78) |
| Intake types `CYCLE_INIT` and `CORRECTION_REQUEST`, `vw_crc_current_flags` | Intake is ADHOC only with a request window; corrections are a `CORRECTION` override (D-79) |

| Added | Purpose |
|---|---|
| `Req_Dt_Key` in the CRC and extract grain | One batch and one extract per run date, so the same report period can run daily (D-77) |
| `Valid_Thru_Dt_Key`, `Rsn` on the override | How long an approval may be used, and why it was asked for (D-74) |
| Extract `Closed_By`, `Close_Warning_Txt`, `Closed_Data_Signature`, `Regenerate_Required_Ind` | Who closed the run, with which warnings, over which data, and whether it must be generated again (D-41) |
| Intake `Req_Start_Dt_Key`, `Req_End_Dt_Key`, `Last_Created_Dt_Key`, `IN_PROGRESS` | The ad-hoc request window (D-79) |
| `AUTO_CLOSE_EXTRACTS` | Whether `evaluate-extracts` closes AUTO-eligible runs or only refreshes them |

## 9. What changed in v6

| Removed | Now |
|---|---|
| `ComplianceEventType` table and `sql/seed.sql` | The vocabulary is code: `audit.BATCH_EVENTS` / `audit.AUDIT_EVENTS` |
| Every `CHECK` constraint, the crosswalk `EXCLUDE` constraint and the `btree_gist` extension | Values are validated in code and by `validate-config` (run type rules, overlapping crosswalk windows) |
| `Src_ID` | `Src_ID` in every table and in the GRE `run_params` (`src_id`) |
| File config `Project_Alias`, `Table_Alias`, `Src_Alias`, `{PROJECT}` / `{TABLE}` / `{SRC}` | The template spells them out; one template per config row already identifies the source |
| File config `Src_File_Ty`, `Line_Term_Cd`, `Core_Tblnm`, `S3_Quarantine_Path`, `Notify_Channel_Cd`, `Bus_Email_Id`, `Bus_Usr_Grp_Nm`, `Dlvry_Ownr_Grp_Nm`, `Email_Cntnt_Txt` | Type = template extension; LF/CRLF both read; core table = `Table_Nm`; one `QUARANTINE_URI` setting; email only; unused columns dropped |
| Intake `Req_Ty`, `Rsn`, `Intake_Stat`, `Error_Txt`, `Processed_Dtts`, `Last_Created_Dt_Key`, `Requested_By`, `Requested_Dtts`, text `Intake_ID` | `Last_Run_Dt_Key`; outcomes in the audit tables; `Created_By` / `Created_Dtts`; identity `Intake_ID` |
| CRC `Created_By`, `Cmplnc_Vrsn` | Follows from the run type's category; the version is part of `Btch_ID` |
| Extract `Extract_Stat`, `Included_Src_Cds`, `Missing_Src_Cds`, `Carried_Src_Cds`, `Close_Warning_Txt`, `Combine_Run_Cnt`, `Combine_Last_Trigger_Cd` | Read from the batches / counts; warnings in the close audit event |
| File load `Received_Dtts`, `Parsed_*`, `Src_Rcd_Cnt`, `Trlr_Rcd_Cnt`, `Core_Appended_Cnt`, `Core_Disabled_Cnt`, `Attempt_Cnt`, `Heartbeat_Dtts`, `Promoted_Dtts` | `Created_Dtts` / `Updated_Dtts`; period and run type from the batch; counts in the `FILE_PROMOTED` event text |
| Override copies of the batch (`Project_Cd` … `Req_Dt_Key`), `Rejected_By` / `Rejected_Dtts` | Read through `Req_ID`; `Reviewed_By` / `Reviewed_Dtts` cover approve and reject |
| Audit `Event_Ctgy`, file detail `Entry_Ty`, `Received_File_Ref` | Category from the vocabulary; `Ovrd_ID` marks manual decisions; the file is the `Load_ID` |
| SNS notifications (`SNS_TOPIC_ARN`) | Every notification is an email (`NOTIFY_BACKEND=log|ses`) |

| Added / renamed | Purpose |
|---|---|
| `ComplianceProject` (`Project_Cd`, `Project_Desc`) | One row per project; every table's `Project_Cd` references it |
| `Stg_Tblnm` → `Stg_Table_Nm`, `Effective_*_Dt` → `Effective_*_Dt_Key`, `Closed_By` → `Extract_Closed_By`, `Failed_Rule_Refs` → `Failed_Rule_List`, `Rsn` → `Rsn_Txt`, `Description` / `Detail_Txt` → `Event_Txt` | Class-word suffixes as in `ComplianceRequestControl` |
| Named constraints (`pk_`, `fk_`, `uq_`, `ix_`) and audit columns on every table people maintain | PostgreSQL DDL standard |

## 10. Extract control removed

| Removed | Now |
|---|---|
| `ComplianceExtractControl`, `extract.py`, CRC and audit `Extract_ID` | Each batch closes on its own (`closing.py`); the extract is a separate process (D-76) |
| `evaluate-extracts`, `refresh-extract`, `close-extract` | `close-batches` (SLA sweep) and `close-batch --btch-id --closed-by` |
| Period-level rules, `Rule_Scope_Cd`, `run --module RULES_TRIGGER`, `PERIOD_RULES_MODE` | Only file rules remain (still bound at project / table / run type / source level) |
| `EXTRACT_GATING_MODE`, `AUTO_CLOSE_EXTRACTS`, eligibility, combine, data signatures, `Regenerate_Required_Ind` | The run type's `SLA_Days` sets the hold; a late arrival or correction is visible as `LATE_ARRIVAL_PROMOTED` / `CORRECTION_PROMOTED` on the batch timeline |
| `BATCH_CREATE_SKIPPED_EXTRACT_CLOSED`, `EXTRACT_*`, `PERIOD_RULES_FAILED` events | `BATCH_CLOSE_BLOCKED`, `BATCH_CLOSE_DEFERRED_LOCKED` |
