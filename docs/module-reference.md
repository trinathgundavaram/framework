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
| **Extract (a run)** | One (project, table, run type, report period, run date) across all sources (`ComplianceExtractControl`). |
| **Job setting** | A value from `settings.py` (job argument > environment > `.env` > default). Replaces the removed settings, connection, period, policy and job-parameter tables. |

**Rules that apply to every module**
- **Time:** nothing reads the system clock directly. Time comes from `common.Clock`, so every command can run with `--as-of`.
- **Transactions:** connections are autocommit. Every unit of work is an explicit `with conn.transaction():` block. Because staging, core and control tables share one database, a promotion (core swap + CRC update + audit) commits or rolls back as one transaction.
- **Audit:** only `audit.EventLogger` writes the two audit tables. Audit text holds ids, codes and counts, never file content (no PHI).
- **Business outcome vs failure:** a rejected file, a failed rule or a blocked close is a *returned result*, recorded in the audit table. Only technical problems raise exceptions, so the caller can retry.
- **Project-agnostic:** no module contains project, table or source names.
- **The framework does not generate the extract** (D-76). It closes the run; the project's job chain reads the closed row and builds the submission.

---

## 2. Architecture at a glance

```
 cli.py ──► settings.py (job args > env > .env > defaults; DB from .env or Secrets Manager)
   │
   ▼
 app.py (App: one connection + services; wiring only)
   │
   ├─ batches.py    create-batches (period_sql.py), ad-hoc intake windows
   ├─ ingest.py     file pipeline (one object or a path sweep) + §8 decision tables ──► load.py (read, stage, swap)
   ├─ overrides.py  REUSE decisions: apply and expire
   ├─ extract.py    eligibility, refresh/combine, close, SLA sweep
   └─ audit.py      EventLogger, NotificationDispatcher

 shared: common.py (errors, clock, Btch_ID, Req_Stat), config.py (config rows, templates, validator),
         db.py (schema install, advisory locks), adapters.py (S3/local store, GRE, SES/SNS)
```

**Layering rule:** `common`, `settings`, `db`, `load`, `config`, `adapters` and `audit` never import a service module (`batches`, `ingest`, `overrides`, `extract`, `app`, `cli`).

**Health is an instance of the same rule.** `App.health()` (§15.3) carries no table-specific SQL; it only composes the dict returned by each service that owns the tables in question: `IngestPipeline.health()` (`ComplianceFileLoad`), `DecisionProcessor.health()` (`ComplianceBatchOverride`) and `ExtractControlService.health()` (`ComplianceExtractControl`). Apply the same split to future operational reports or cross-cutting reads: the generic composition goes in `app.py`; the query that knows a table's columns and status values goes in the module that already owns that table (§7).

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
| `ingest-file` | P5, P6 | `ingest.IngestPipeline` → `config.TemplateMatcher` → `adapters` (store) → `load.stage` → `adapters` (rules) → `ingest.decide` → `load.swap` → `extract.ExtractControlService.refresh` (early completion or regenerate flag) |
| `ingest-path` | P5, P6 | `ingest.IngestPipeline.process_path` → `adapters` (store `list_objects`) → `IngestPipeline.process_file` per object found (same chain as `ingest-file`, once per object; several objects may resolve to different configs and different batches, D-26/D-33) |
| `process-decisions` | P7 | `overrides.DecisionProcessor` → `extract` refresh |
| `evaluate-extracts` | P10 | `extract.ExtractEvaluator` → `ExtractControlService.refresh` / `close` |
| `refresh-extract` | P9 | `extract.ExtractControlService.refresh` (`MANUAL_REFRESH`) |
| `close-extract` | P11 | `extract.ExtractControlService.close` |
| `notify` | P13 | `audit.NotificationDispatcher` → `adapters` (log / SES / SNS) |
| `health` | ops | `app.App.health` → `ingest.IngestPipeline.health` + `overrides.DecisionProcessor.health` + `extract.ExtractControlService.health` |

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
- Creates each service on first use and wires the hooks: a promoted file refreshes its extract (`EARLY_COMPLETE`); a promotion into a **closed** batch calls `evaluator.after_late_promotion` (`LATE_PROMOTION`, sets `Regenerate_Required_Ind`, D-41); an override decision refreshes its extract (`OVERRIDE_DECISION`).
- `health()`: merges the dict returned by `pipeline.health()`, `decisions.health()` and `control.health()` — stale loads and quarantine counts by reason (ingest), pending reviews and overrides expiring within 7 days (overrides), extracts past their SLA hold and still open and runs needing regeneration (extract) (§15.3). `app.py` itself carries none of that SQL: each service reports on the tables it owns (§7), the same split the layering rule already applies to imports.

### `settings.py`
- `Settings`: every runtime and job-level setting with its default, and where each value came from (`argument`, `env`, `.env`, `default`).
- `Settings.load(env, overrides, env_file)`: layers job arguments > `FRAMEWORK_<NAME>` environment > `.env` file > default; converts text to the declared type; validates enumerations and the schema name.
- `db_conninfo()` / `connect()`: the database from `FRAMEWORK_DB_SECRET_NAME` (Secrets Manager JSON) and/or `FRAMEWORK_DB_DSN` / `FRAMEWORK_DB_*`; explicit values override the secret; `search_path` is set to the metadata schema. Passwords never appear in `describe` output.
- Close-related settings: `EXTRACT_GATING_MODE`, `PERIOD_RULES_MODE`, `AUTO_CLOSE_EXTRACTS`. There are no extract-job settings (D-76).
- **Replaces:** `ComplianceFrameworkSetting`, `ComplianceDbConnection`, `framework.ini`.

### `common.py`
- Exception hierarchy (`ConfigError`, `LockTimeout`, `TechnicalFailure`, `FileRejected`, `CloseBlocked`, `CloseDeferred`, ...).
- `Clock`, `FixedClock`, `parse_as_of`.
- `build_btch_id`, `earliest_close_date(req_dt, sla_days)` — computed, never stored (D-77).
- `Req_Stat` values and `TRANSITIONS` / `check_transition` (§6.1). **Replaces** `ComplianceRequestStatus` and `ComplianceRequestStatusTransition`.

### `db.py`
- `connect(dsn, schema)` for tests and ad-hoc use; `init_db` creates the schema, installs `btree_gist`, applies `schema.sql` once and re-applies `seed.sql`.
- Advisory locks (§12.2): `held` (session lock with timeout), `try_lock` / `unlock`, `xact_lock`; key builders `extract_key`, `batch_key`, `seq_key`. Lock order is always EXT → BTCH.

### `config.py`
- Typed rows: `RunType` (incl. `sla_days`, `carry_fwd`), `XwalkRow`, `FileConfig`, `RuleBinding`.
- Read access: `run_type(s)`, `xwalk_rows`, `effective_xwalk`, `effective_sources`, `active_file_configs`, `file_config`, `file_config_by_id`, `rule_bindings`.
- Filename templates (§9): `parse_template`, `compile_template`, `render`, `TemplateMatcher` (unique match or `MatchError` with the quarantine code).
- `validate_all` (P1): event vocabulary, crosswalk ↔ file config coverage, inactive run types (warning), template grammar and extension, S3 paths, staging/core tables and their framework columns, template overlap, alias collisions.
- **Out of scope:** schedules, periods, time zones — these are job settings now.

### `batches.py`
- CRC and extract rows: `find_batch` / `find_extract` (per report period **and run date**), `get_batch`, `batches_of_extract`, `promoted_load` (the batch's current data, D-75), `create_batch` (idempotent per run date; skipped when the run is already closed).
- Report period: `period_sql(name, period_file)` returns the SQL for a name from `period_sql.py` or from a project file that defines `PERIOD_SQL`; `compute_period` runs it for the run date and checks lookback parameters.
- `create_batches(project, run type, period, [table], ...)` (P2): run date = today in `BUSINESS_TZ` (or `--as-of`); one batch per effective crosswalk row per run date; `Required_Src_Cnt` per table.
- `IntakeProcessor` (P4, D-79): ADHOC only; creates the batches of today's run date while the intake's `[Req_Start_Dt_Key, Req_End_Dt_Key]` window is open, records `Last_Created_Dt_Key`, and finishes the row as `COMPLETED` or `FAILED`.

### `period_sql.py`
- `PERIOD_SQL`: name → one `SELECT ... AS rpt_start, ... AS rpt_end` using `%(sched_dt)s`, `%(lookback_days)s`, `%(lookback_weeks)s`. **Replaces** `CompliancePeriodStrategy`.

### `ingest.py`
- `decide(ResolutionInput)` (§8): pure decision tables O-1 … O-5 (open batch) and C-1 … C-4 (closed batch); `required_override_ty(has_data)` says which override type a closed batch needs.
- Batch selection (D-78): the open batch of the grain with the latest run date ≤ today; otherwise the most recent closed batch, which needs an approved, still-valid override. Without one the file is quarantined as `FILE_REJECTED_BATCH_CLOSED` — a **retryable** quarantine, so re-delivering the object after the approval reprocesses the same `Load_ID`.
- `IngestPipeline.process_file` (P5): registers the object (C0 idempotency), matches the template, checks location, run type, effective crosswalk and batch, stages the file, runs FILE_LEVEL rules **when bindings exist** (mode `FILE_RULES_MODE`), resolves, promotes in the same transaction (superseding the previous `PROMOTED` load first, D-75), archives or quarantines, and handles replays and technical failures.
- `IngestPipeline.process_path(bucket=None, prefix=None)`: lists objects at one location (`adapters.ObjectStore.list_objects`), or — with neither argument — at the distinct inbound location of every active file config (`_configured_locations`, de-duplicated, since several source configs commonly share one folder), and calls `process_file` once per object found. Each object still resolves to exactly one config and exactly one batch (D-26, D-33), under that batch's own lock, exactly as a separate `ingest-file` call would; a listing problem or a per-object technical failure is recorded on the returned `PathIngestSummary` and does not stop the rest of the sweep.
- A file promoted into an open carried-forward batch clears `Reuse_Btch_ID` and logs `CARRY_FORWARD_REMOVED`; a promotion into a closed batch triggers the regenerate flag through the `on_late_promotion` hook.
- `health()`: stale loads and quarantine counts by reason — `ingest.py` owns `ComplianceFileLoad` (§7), so its health queries live here rather than in `app.py`.

### `load.py`
- Table introspection: `columns`, `staging_business_columns`, `core_insert_columns` (identity / generated / serial columns are skipped).
- File reading (§9.4 C12–C14): `read_file`, `read_delimited`, `scan_delimited` (streaming), `read_with_pandas` (xlsx / parquet), `sanitize_db_error`.
- `stage(conn, settings, ...)`: delete staging rows of the batch (D-05) and load the file; `LOAD_ENGINE=PANDAS` (reader + COPY) or `SPARK` (streaming scan + JDBC append; needs `SPARK_JDBC_URL`).
- `staged_row_count`; `swap` (D-01) disables the current core rows of the batch and appends the load's rows, checking the row count.

### `overrides.py`
- `DecisionProcessor.run` (P7, D-74): handles `REUSE` only — `LATE_ARRIVAL` and `CORRECTION` are read by the ingest pipeline.
  - **apply:** an approved, still-valid `REUSE` on an open batch without data → checks `Carry_Fwd_Ind`, that the batch has no promoted load, and that a source batch exists (the requested `Reuse_Btch_ID`, else the latest earlier closed batch with data; a carried batch resolves to its own source). Sets CRC `Resolution_Ty='CARRY_FORWARD'`, `Reuse_Btch_ID`, `Req_Stat='CARRIED_FORWARD'`; logs `OVERRIDE_APPROVED` + `CARRY_FORWARD_APPLIED`; refreshes the extract. An invalid row → `OVERRIDE_INVALID_DETECTED`, batch unchanged.
  - **expire:** an open carried batch whose override ran out (`Valid_Thru_Dt_Key < today`), was rejected or is gone → back to `PENDING`, `OVERRIDE_EXPIRED` + `CARRY_FORWARD_REMOVED`, extract refreshed.
- There is no revoke and no promotion state: stopping an override is a date change (`sql/approvals.sql` template 6).
- `health()`: pending reviews and approved overrides expiring within 7 days, across all three override types — `overrides.py` owns `ComplianceBatchOverride` (§7), so its health queries live here rather than in `app.py`.

### `extract.py`
- `compute_eligibility` (§11.2): SLA hold (`earliest_close_dt`, computed), STRICT_ALL_PASS / BEST_EFFORT, period-rule status. Carried sources count as received.
- `ExtractControlService.refresh` (P9): recount (received = NEW_FILE + CARRY_FORWARD), `Carried_Src_Cds`, data signature over (Btch_ID, Load_ID) pairs — a carried batch contributes the reused batch's pair — combine + PERIOD_LEVEL rules (mode `PERIOD_RULES_MODE`), regenerate flag when the data of a closed run changed, eligibility.
- `hold_date(extract)`: `Req_Dt_Key + (SLA_Days − 1)` from the run type (D-77).
- `ExtractControlService.close` (§11.3): refresh, check eligibility (`CloseBlocked` otherwise), try-lock every open batch (`CloseDeferred`), then close every batch (`EXCEPTION_PENDING` → `COMPLETED_WITH_EXCEPTION`; NEW_FILE or CARRY_FORWARD → `COMPLETED`; otherwise `DATA_NOT_PROVIDED` / `MISSING`) and the extract, storing `Closed_By`, `Close_Warning_Txt` and `Closed_Data_Signature`.
- `ExtractEvaluator.run(project, table, run type)` (P10): sweeps open extracts whose hold has passed (SLA joined from `ComplianceRunType`), closes the AUTO-eligible ones when `AUTO_CLOSE_EXTRACTS` is on, and reports the runs that need regeneration; `after_late_promotion` refreshes a closed run after a late arrival or correction.
- `ExtractControlService.health()`: runs past their SLA hold and still open, and runs needing regeneration — `extract.py` owns `ComplianceExtractControl` (§7), so its health queries live here rather than in `app.py`.

### `adapters.py`
- Object storage: `ObjectStore`, `LocalObjectStore` (`local://` and tests), `S3ObjectStore`, `parse_uri`, `basename`, `dirname`, `sha256_file`, `build_object_store`.
- `ObjectStore.list_objects(bucket, prefix)`: every object directly under a location, non-recursive (Q-03: inbound files sit at the template's root, no sub-folders) — `LocalObjectStore` walks the directory, `S3ObjectStore` paginates `list_objects_v2` with `Delimiter="/"`. Backs `ingest.IngestPipeline.process_path`.
- Rules engine (Q-12): `RuleOutcome`, `CallableRuleEngine` (applies GATE / ANNOTATE), `NoRulesEngine`, `build_rule_engine` (`RULE_ENGINE=gre|none|module:Class`, `GRE_ENTRYPOINT`).
- Notification channels (D-54): `LogChannel`, `AwsChannel` (SES + SNS `SNS_TOPIC_ARN`), `build_channel`.
- There are no extract connectors (D-76).

### `audit.py`
- `EventLogger`: `batch_event` → `ComplianceRequestFileDetail`, `audit` → `CMS_ComplianceExceptionsAudit`; checks the event type and its target table.
- `NotificationDispatcher` (P13): unsent notifiable events; recipients from the file config (failure / success lists) or `DEFAULT_NOTIFY_EMAILS`; channel from the file config or the event type; marks `Notified_Ind`.

---

## 5. SQL files

| File | Purpose |
|---|---|
| `sql/schema.sql` | All 13 framework tables, constraints and indexes. Schema-unqualified, `CREATE`-only (no `ALTER` while the model is in review); applied once by `init-db`. |
| `sql/seed.sql` | Event vocabulary (target table, category, severity, notify flag). Re-run by every `init-db`. |
| `sql/approvals.sql` | The six manual override templates (insert an approved `REUSE` / `LATE_ARRIVAL` / `CORRECTION`, approve, reject, stop early). Each statement must report 1 row. |

---

## 6. Tests

| File | Covers |
|---|---|
| `conftest.py` | `conn` fixture: recreates the metadata schema and the sample `stg_t` / `core_t` tables in `TEST_DATABASE_URL`. |
| `helpers.py` | Synthetic configuration seed, job-level settings (`make_app`), fake rules engine, `create_batches`, file builders, `add_override` / `stop_override`. |
| `test_units.py` | No database: templates, readers, local store (incl. `list_objects`), eligibility, §8 tables and `required_override_ty`, `PathIngestSummary.tally`, Btch_ID, Req_Stat transitions, settings precedence and `.env`, DB conninfo from DSN / secret, period SQL lookup. |
| `test_batches.py` | Every period, lookback checks, project period file, create-batches (one batch per run date, daily runs of one period, re-run by `--as-of`, effective windows, scope, closed runs skipped), the removed CRC columns, ad-hoc intake windows. |
| `test_ingest.py` | Promotion, replacement, quarantine reasons, structural failures, duplicates, zero records, GATE / ANNOTATE, no bindings, technical failure and replay, closed-batch overrides (late arrival, correction, retry after approval), batch selection by run date, `process_path` (one location, every configured location, mixed outcomes, listing/technical errors that don't stop the sweep). |
| `test_overrides.py` | `REUSE`: apply, invalid cases, pending until approved, expiry, replacement by a real file, reuse chains; `LATE_ARRIVAL` / `CORRECTION` rows are left to the pipeline. |
| `test_extract.py` | Automatic close after the hold, STRICT partial and rule failures, BEST_EFFORT manual close with acknowledged warnings, zero data, deferred close on a locked batch, period-rule errors, sweep scope and `AUTO_CLOSE_EXTRACTS`, per-run-date holds, regenerate reporting. |
| `test_config_cli.py` | Validator, notifications, CLI end to end (with `.env`, `--set` and `close-extract`), `ingest-path` end to end, the composed `health` report, rules adapter contract. |

---

## 7. Who writes which table

| Table | Written by |
|---|---|
| `ComplianceRequestControl` | `batches.create_batch` (create), `ingest` (status, resolution), `overrides` (carry-forward apply / expire), `extract` (close) |
| `ComplianceExtractControl` | `batches` (create, required count), `extract` (counts, rules, eligibility, close, regenerate flag) |
| `ComplianceFileLoad` | `ingest` (received, promoted, superseded, quarantined) |
| `ComplianceBatchOverride` | people via `sql/approvals.sql`; read by `ingest` and `overrides` |
| `ComplianceRequestInTake` | people / upstream systems (new rows); `batches.IntakeProcessor` (state, `Last_Created_Dt_Key`, result) |
| `ComplianceRequestFileDetail`, `CMS_ComplianceExceptionsAudit` | `audit.EventLogger` only (`NotificationDispatcher` sets `Notified_Ind`) |
| `ComplianceEventType` | `db.init_db` (`seed.sql`) |
| `ComplianceSourceSystem`, `ComplianceRunType`, `ComplianceDataSetSourceXwalk`, `ComplianceSourceFileConfig`, `ComplianceRuleBinding` | people via SQL or the Glue metadata-load job |
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
