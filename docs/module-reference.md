# CMS Compliance Framework: Module Reference

This document explains, file by file, what each script in `src/framework/` does, what it is responsible for, and what it deliberately leaves to other modules. It matches the code on branch `feature/framework-package` (design v3.2).

- **Design:** [`design/cms-compliance-framework-design.md`](design/cms-compliance-framework-design.md). `D-nn` = decision, `Q-nn` = open question, `§n` = design section.
- **Setup, configuration, commands:** [`framework-package.md`](framework-package.md).

## Contents

1. [Conventions](#1-conventions)
2. [Architecture at a glance](#2-architecture-at-a-glance)
3. [Command → module map](#3-command--module-map)
4. [Entry points and wiring](#4-entry-points-and-wiring): `cli.py`, `app.py`
5. [Infrastructure](#5-infrastructure): `settings.py`, `connections.py`, `db.py`, `locks.py`, `clock.py`, `errors.py`, `health.py`
6. [Configuration](#6-configuration-config): `config/`
7. [Shared model](#7-shared-model-common-audit): `common/`, `audit/`
8. [Batches](#8-batches-batches): `batches/`
9. [File ingest](#9-file-ingest-ingest): `ingest/`
10. [Staging and promotion](#10-staging-and-promotion-load): `load/`
11. [Manual decisions](#11-manual-decisions-overrides): `overrides/`
12. [Rules engine adapter](#12-rules-engine-adapter-validation): `validation/`
13. [Extracts](#13-extracts-extract): `extract/`
14. [Notifications and storage](#14-notifications-and-storage): `notify/`, `storage/`
15. [SQL files](#15-sql-files-sql)
16. [Tests](#16-tests-tests)
17. [Who writes which table](#17-who-writes-which-table)

---

## 1. Conventions

| Term | Meaning |
|---|---|
| **META** | The metadata database: configuration, control and audit tables, in the schema named by `METADATA_SCHEMA` (D-65). |
| **DATA** | The database holding a project's staging and core tables: the connection named by `ComplianceSourceFileConfig.Target_Connection_Nm`, or META when that column is NULL (D-66). |
| **CRC** | `ComplianceRequestControl`, one row per batch. |
| **Batch** | One (project, table, source, run type, report start, report end). Identified by `Btch_ID`. |
| **Load** | One physical file (`ComplianceFileLoad`, identified by `Load_ID`). |
| **Extract** | One (project, table, run type, report period) across all sources (`ComplianceExtractControl`). |
| **Service** | A class that owns one process flow (§7) and is called by exactly one CLI command, sometimes also by another service. |

**Rules that apply to every module**
- **Time:** nothing reads the system clock directly. Time comes from `clock.py`, so every command can run with `--as-of`.
- **Transactions:** connections are autocommit. Every unit of work is an explicit `with conn.transaction():` block.
- **Audit:** only `audit/event_logger.py` writes the two audit tables. Audit text holds ids, codes and counts, never file content (no PHI).
- **Business outcome vs failure:** a rejected file, a failed rule or a blocked trigger is a *returned result*, recorded in the audit table. Only technical problems (database, object store, rules engine errors) raise exceptions, so the caller can retry.
- **Project-agnostic:** no module contains project, table or source names. Everything specific comes from configuration rows.

---

## 2. Architecture at a glance

```
            ┌─────────────── cli.py  (one command = one service call, exit code)
            │
            ▼
         app.py  ── bootstrap: settings.py → connections.py (META) → ComplianceFrameworkSetting
            │
   ┌────────┼──────────────┬──────────────────┬──────────────────┬───────────────────┐
   ▼        ▼              ▼                  ▼                  ▼                   ▼
batches/  ingest/       overrides/         extract/           notify/            health.py
scheduler pipeline      decision_processor control/eligibility notifier
intake      │                │              trigger/evaluator
            │                │                  │
            ▼                ▼                  ▼
        load/ (engines, promoter, archive_restager)      extract/connectors (Glue / HTTP)
            │
            ▼
   validation/gre_adapter (FILE_LEVEL and PERIOD_LEVEL rules)

 Shared by all: config/ (read config, templates, validator), common/ (Btch_ID, status model),
 audit/event_logger, locks.py, clock.py, errors.py, storage/object_store.py
```

**Layering rule:** services depend on `config/`, `common/`, `audit/`, `load/` and `storage/`. Those shared modules never import a service. The only exception is `config/validator.py`, which imports the list of allowed extract attributes from `extract/trigger.py` so the two cannot drift apart.

---

## 3. Command → module map

| Command | Flow (§7) | Modules run, in order | Databases |
|---|---|---|---|
| `init-db` | deploy | `cli` → `settings` → `connections` (META spec) → `db.init_db` → `sql/ddl`, `sql/seed` | META |
| `show-config` | ops | `cli` → `app.bootstrap` → prints `Settings` + META spec | META |
| `test-connections` | ops | `cli._test_connections` → `connections` (META + every data connection) | META, DATA |
| `validate-config` | P1 | `app` → `config.validator` → `config.repository`, `config.templates`, `batches.cron`, `batches.period_strategies`, `load.tables` | META, DATA (table checks) |
| `create-batches` | P2 | `batches.scheduler.run_scheduler` → `batches.cron` → `batches.period_strategies` → `batches.crc_repository` → `common.btch_id` | META |
| `catchup` | P3 | `batches.scheduler.run_catchup` (same chain, longer window) | META |
| `process-intake` | P4 | `batches.intake_processor` → `batches.crc_repository` | META |
| `ingest-file` | P5, P6 | `ingest.pipeline` → `config.templates` → `storage` → `load.engine.*` → `ingest.file_reader` → `validation.gre_adapter` → `ingest.resolution_engine` → `load.promoter` → (early completion) `extract.control` | META, DATA |
| `process-decisions` | P7, P8 | `overrides.decision_processor` → `load.archive_restager` (if needed) → `load.promoter` → `extract.control` / `extract.evaluator` | META, DATA |
| `evaluate-extracts` | P10 | `extract.evaluator` → `extract.trigger.reconcile` → `extract.trigger.fire` → `extract.control` → `extract.eligibility` → `extract.connectors.*` | META, DATA (period rules) |
| `refresh-extract` | P9 | `extract.control.refresh` → `validation.gre_adapter` → `extract.eligibility` | META, DATA |
| `trigger-extract` | P11 | `extract.trigger.fire` (MANUAL) → same chain as above | META, DATA |
| `resolve-trigger` | P12 | `extract.trigger.resolve` | META |
| `notify` | P13 | `notify.notifier` → `config.repository` | META |
| `health` | ops | `health.report` | META |

Every command except `init-db` and `test-connections` starts through `app.App.from_settings`. Exit codes: 0 = ok, 1 = completed with problems, 2 = blocked or framework error.

---

## 4. Entry points and wiring

### `cli.py`
- **Purpose:** the command-line interface (`framework <command>`). Parses arguments, builds the app, calls **one** service, prints the result as JSON and sets the exit code.
- **In scope:**
  - The argument parser for every command in §3.
  - `init-db`: resolves only the META connection (the schema may not exist yet) and calls `db.init_db`.
  - `test-connections`: opens META, checks the schema, then opens every active `ComplianceDbConnection` row and every connection referenced by an active file config; each is reported separately.
  - `show-config`: prints every setting with its value and source, and the META connection without its password.
  - `validate-config`: prints the issues; on errors writes one `CONFIG_VALIDATION_FAILED` audit event.
  - Maps `FrameworkError`, `ValueError` and a missing config file to exit code 2.
- **Out of scope:** any business logic, SQL beyond the `test-connections` connection list, scheduling (an external scheduler calls the commands).
- **Called by:** the `framework` console script (`pyproject.toml`), and later Glue / Step Functions wrappers (D-13).

### `app.py`
- **Purpose:** start-up and dependency wiring.
- **`bootstrap(settings, config, env, secret_loader)`:**
  1. Loads settings from the environment and config file.
  2. Builds a `ConnectionManager` and opens META.
  3. Fails with `ConfigError` if the metadata schema is not initialised.
  4. Applies `ComplianceFrameworkSetting` values (environment and config file still win); an unconvertible value is a `ConfigError`, an unknown name only a warning.
  5. Closes the connections again if any step fails.
- **`App`:** holds the connection manager, clock, settings, object store and rules engine, and creates each service on first use (`control`, `trigger`, `evaluator`, `pipeline`, `decisions`, `intake`, `notifier()`).
  - Services that touch staging/core tables (`pipeline`, `decisions`, `control`) receive the `ConnectionManager`. The others receive only the META connection.
  - Connects the hooks between services: a promoted file triggers an early extract refresh (`EARLY_COMPLETE`), a waiver decision refreshes its extract, and a promoted reopen calls `evaluator.after_reopen_promotion` (D-41).
  - Usable as a context manager; `close()` closes every connection.
- **Out of scope:** reading configuration rows, business rules.
- **Design:** §3, D-13, D-65 – D-67.

---

## 5. Infrastructure

### `settings.py`
- **Purpose:** every runtime setting, its type, default and description, and where its value came from.
- **In scope:**
  - The `Settings` dataclass (one field per setting; `sources` records `env` / `file` / `metadata` per field).
  - `Settings.load(env, config)`: applies the config file `[settings]` section, then `FRAMEWORK_<NAME>` environment variables.
  - `apply_metadata(rows)`: applies `ComplianceFrameworkSetting` rows only where environment and file did not set the value; ignores bootstrap names and NULL values; returns unknown names.
  - `coerce(name, text)`: converts text to the declared type (bool, int, date, list, dict, upper-case enums); raises `ValueError`.
  - `read_config_file(path)`: INI reader. An explicit path that does not exist is an error; the default `./framework.ini` is optional.
  - `DESCRIPTIONS`: seeded into the settings table by `init-db`.
  - `BOOTSTRAP`: `config_file`, `metadata_schema`, `aws_region`, which never come from metadata.
- **Out of scope:** database connection settings (see `connections.py`), reading the settings table (see `db.load_metadata_settings`).
- **Design:** D-67; the individual settings map to Q-02 – Q-18.

### `connections.py`
- **Purpose:** decide how to connect to META and to each named DATA database, and keep those connections open for the run.
- **In scope:**
  - `ConnSpec`: host, port, database, user, password, sslmode, timeout, `search_path`, and the layer each value came from. Builds a psycopg conninfo string, a JDBC URL (Spark) and a password-free description.
  - `ConnectionResolver`: merges the layers.
    - META: environment `FRAMEWORK_DB_*` > config `[metadata_db]` > Secrets Manager.
    - DATA `<name>`: environment `FRAMEWORK_CONN_<NAME>_*` > config `[connection:<name>]` > `ComplianceDbConnection` row > Secrets Manager.
    - A DSN is accepted in the environment and file layers; explicit keys override DSN values. `PASSWORD_ENV` / `Password_Env_Var` name a variable that holds the password.
    - The secret used is the one named by the highest layer that names one; it only fills fields no other layer set.
    - META always gets `search_path = <metadata_schema>, public`.
  - `open_connection(spec)`: direct autocommit connection with dict rows (D-55: no poolers, because session advisory locks depend on it).
  - `ConnectionManager`: opens META lazily, looks up `ComplianceDbConnection` rows (active only), caches one connection per name, `for_config(cfg)` returns the DATA connection for a file config, `register()` injects a connection (tests, embedding), `close()`.
  - `spec_from_connection`: describes an injected connection (used for Spark JDBC details).
- **Out of scope:** creating connection rows (manual SQL), connection pooling, the metadata schema itself.
- **Failure handling:** missing database name in every layer, unreadable secret, invalid DSN, unreachable server, or an unknown/inactive connection name → `ConfigError` with a password-free message.
- **Design:** D-55, D-65, D-66.

### `db.py`
- **Purpose:** schema installation and small database helpers.
- **In scope:**
  - `init_db(conn, schema)`, in one transaction:
    1. Validates the schema name, creates the schema, sets `search_path` to it.
    2. Installs `btree_gist` once per database, in `public` (falls back to the metadata schema if `public` is not writable), so dropping one schema never removes another schema's constraint.
    3. Applies `sql/ddl/*.sql` only if the schema has no framework tables yet.
    4. Applies every `sql/seed/*.sql` (idempotent), optionally skipping the provisional status file.
    5. Inserts one `ComplianceFrameworkSetting` row per runtime setting with its default, without overwriting existing rows.
  - `schema_exists`, `load_metadata_settings` (active rows only), `connect(dsn, schema)` for ad-hoc and test use.
- **Out of scope:** schema migrations for later versions (DDL is applied only to a new schema), creating the database or roles.
- **Design:** Appendix A, Q-11.

### `locks.py`
- **Purpose:** PostgreSQL session advisory locks (§12.2).
- **In scope:** key builders `EXT:<Extract_ID>`, `BTCH:<Btch_ID>`, `SEQ:<project|table|src|run type>`; `try_lock`, `unlock`, `acquire` (polls until a timeout → `LockTimeout`), `held(...)` context manager, and `xact_lock` (transaction-scoped, used for `Seq` allocation).
- **Rules:** lock order is always EXT → BTCH. Locks are always taken on META, even when the data is in another database. They disappear if the session dies.
- **Out of scope:** deciding what to lock (the services do).

### `clock.py`
- **Purpose:** the only source of "now". `Clock` (real time), `FixedClock` (tests and `--as-of`), `parse_as_of` (ISO-8601; naive values are UTC). `today(tz)` returns the date in a business timezone.

### `errors.py`
- **Purpose:** the exception hierarchy. `FrameworkError` is the base.
- **Configuration and control flow:** `ConfigError`, `LockTimeout`, `InvalidStatusTransition`.
- **Technical failures (retryable):** `TechnicalFailure`, `RowCountMismatch`, `RuleEngineNotConfigured`.
- **File rejection:** `FileRejected(event_ty, message)`. Carries the quarantine reason code; the pipeline turns it into a quarantine result, not a failure.
- **Trigger outcomes:** `TriggerBlocked` (not eligible), `TriggerDeferred` (a batch is locked), `TriggerCallFailed` (call rejected), `TriggerOutcomeUnknown` (needs `resolve-trigger`).

### `health.py`
- **Purpose:** the operational report behind `framework health` (§15.3). Read-only.
- **Returns:**
  - loads with a stale heartbeat;
  - reopens waiting for review;
  - failed reopen promotions;
  - trigger calls still in flight;
  - extracts past their SLA hold and not triggered (Q-17);
  - extracts flagged for re-trigger;
  - quarantine counts by reason.
- **Out of scope:** alerting (a scheduler or monitoring tool acts on the output).

---

## 6. Configuration (`config/`)

### `config/models.py`
- **Purpose:** typed, read-only views of configuration rows: `RunType`, `XwalkRow` (with `effective_on(date)`), `FileConfig` (includes `target_connection_nm`), `ExtractPolicy`, `JobParam`, `RuleBinding`, `PeriodStrategy`. Each has `from_row()` where needed.
- **Out of scope:** database access and validation.

### `config/repository.py`
- **Purpose:** every read of a configuration table, in one place. All functions take a META connection and return models.
- **Functions:** run types; crosswalk rows (all, routine, effective for a date, effective sources for a table/run type); active file configs (all, by source, by `Cfg_ID`, any one for a table); extract policy; job parameters; rule bindings; period strategy; business timezone of an extract; target connection names used by a table.
- **Out of scope:** writes (configuration is maintained with SQL), control tables.
- **Reads:** `ComplianceRunType`, `ComplianceDataSetSourceXwalk`, `ComplianceSourceFileConfig`, `ComplianceExtractPolicy`, `ComplianceExtractJobParam`, `ComplianceRuleBinding`, `CompliancePeriodStrategy`.

### `config/templates.py`
- **Purpose:** filename templates (§9, D-28 – D-37).
- **In scope:**
  - `parse_template`: validates the grammar (each of `{PROJECT} {TABLE} {SRC} {RUNTY} {RPTSTART} {RPTEND} {TS}` exactly once, placeholders separated by literal text, no unknown placeholders).
  - `compile_template`: builds a regex using the row's aliases literally; optional case-insensitivity (Q-03).
  - `render`: builds a filename from a template (validator overlap test, tests).
  - `TemplateMatcher.match(name)`: exactly one active config must match; checks the report dates and `{TS}` are valid and end ≥ start; returns config, run type token, dates and timestamp.
- **Rejections (returned as `MatchError` with a quarantine code):** `FILE_REJECTED_UNPARSEABLE` (no match), `FILE_REJECTED_AMBIGUOUS_TEMPLATE` (several matches), `FILE_REJECTED_INVALID_TOKEN` (bad dates or timestamp).
- **Out of scope:** checking that the run type exists or that a batch exists (the pipeline does that), the inbound folder check (pipeline).

### `config/validator.py`
- **Purpose:** `validate_all(meta_conn, case_sensitive, conns)` checks the whole configuration and returns a list of issues (`ERROR` blocks activation, `WARNING` is informational). Read-only.
- **Checks, by group:**
  - **Settings rows:** values that do not convert (error); unknown names and bootstrap names (warning).
  - **Connections:** unresolvable `ComplianceDbConnection` rows; file configs whose connection cannot be opened.
  - **Status model and events:** the status model loads (Q-01); required event types are seeded.
  - **Run types and crosswalk:** alphanumeric run type codes; known run type; cron present only for ROUTINE and valid; timezone valid; strategy present, shipped and given its required parameters; all sources of an extract share one period definition; a file config and an extract policy exist for each row.
  - **File configs:** template grammar and extension; a crosswalk row exists; Spark is not used with trailers; staging and core tables exist **in the target connection's database** with the framework columns; S3 path format; rule bindings exist when rules are required (warning).
  - **Cross-row:** one `Target_Connection_Nm` per (project, table); no two templates can match the same generated filename; no two configs share the same alias triple.
  - **Extract job parameters:** `EXTRACT_ATTR` parameters name a known attribute.
- **Out of scope:** fixing anything, writing audit events (the CLI writes `CONFIG_VALIDATION_FAILED`), checking database connectivity beyond opening the connection (use `test-connections`).
- **Design:** §5.1, §9.3, P1.

---

## 7. Shared model (`common/`, `audit/`)

### `common/btch_id.py`
- **Purpose:** batch identity (§4).
- `build(...)` → `{Req_Dt_Key:YYYYMMDD}_{Project}_{Table}_{Src}_{Run_Ty}_{Vrsn}_{Seq}`.
- `next_seq(...)`: next sequence number for the creation date; the caller must hold the `SEQ` lock.
- `earliest_close_date(req_dt, sla_days)`: SLA 1 = same day, SLA 2 = next day, calendar days (D-38).

### `common/status.py`
- **Purpose:** `Req_Stat` handling through **abstract states** until the real status list is supplied (Q-01).
- `StatusModel.load(conn)`: reads `ComplianceRequestStatus` (each abstract state must map to exactly one active code) and `ComplianceRequestStatusTransition`.
- `code(state)`, `state(code)`, `check(from, to)` → `InvalidStatusTransition` for an unlisted move.
- **Abstract states:** `S_AWAITING`, `S_VALIDATED` (optional), `S_PROMOTED`, `S_EXCEPTION`, `S_COMPLETE`, `S_COMPLETE_EXCEPTION`, `S_NOT_PROVIDED`.
- **Out of scope:** the actual codes (data in `sql/seed/030_request_status_PROVISIONAL.sql`).

### `audit/event_logger.py`
- **Purpose:** the **only** writer of `ComplianceRequestFileDetail` (batch history) and `CMS_ComplianceExceptionsAudit` (exceptions and operational audit).
- `batch_event(...)`: batch-level history row.
- `audit(...)`: audit row with the event's category and default severity; sets `Notified_Ind = 0` only for event types flagged for notification.
- Checks every event type is seeded and belongs to the table being written (`ConfigError` otherwise). Writes join the caller's open transaction and are mirrored to the Python log.
- **Out of scope:** sending notifications (see `notify/`).
- **Design:** §5.3.

---

## 8. Batches (`batches/`)

### `batches/cron.py`
- **Purpose:** cron handling in the business timezone. `is_valid(expr)`; `fire_times(expr, tz, after, until)` returns DST-aware fire times in the window (croniter).

### `batches/period_strategies.py`
- **Purpose:** report-period calculation (§5.1).
- Loads the strategy's SQL file shipped in `sql/period_strategies/` (single statement only), runs it with `sched_dt`, `lookback_days`, `lookback_weeks`, and checks the result (start and end present, end ≥ start).
- `period_for(conn, xwalk_row, sched_dt)` is the entry point; `strategy_file_exists` is used by the validator.
- **Out of scope:** choosing the strategy (configuration), deciding when to run (scheduler).

### `batches/crc_repository.py`
- **Purpose:** create and look up batches and extract rows.
- **`create_batch(...)`**, idempotent, in one transaction under the `SEQ` lock:
  1. Returns the existing batch if the period already has one (D-30).
  2. Creates the extract row if missing (`ensure_extract`).
  3. Skips creation (`BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED`) if the period's extract was already triggered.
  4. Allocates `Seq`, builds `Btch_ID`, inserts the CRC row in the "awaiting" status with its earliest close date.
  5. Pushes the extract's `Earliest_Trigger_Dt` forward if this batch's hold ends later.
  6. Writes `BATCH_CREATED`.
- **Lookups:** `find_batch`, `get_batch` (optionally `FOR UPDATE`), `find_extract`, `batches_of_extract`.
- **Writes:** `ComplianceRequestControl`, `ComplianceExtractControl`.
- **Called by:** scheduler, intake processor, and lookups from the pipeline, decisions and trigger.

### `batches/scheduler.py`
- **Purpose:** P2 (`create-batches`) and P3 (`catchup`).
- **Steps for each active ROUTINE crosswalk row:**
  1. Expand the cron for the window: `SCHEDULER_WINDOW_HOURS` for the scheduler, `CATCHUP_LOOKBACK_DAYS` for catch-up.
  2. For each fire time, use its date as the scheduled date (D-29b) and skip it if the row is not effective then or it is before `GO_LIVE_DATE`.
  3. Compute the period, count the sources effective on that date (the extract's required count), and call `create_batch`. `Req_Dt_Key` is the actual creation date in the business timezone (D-29).
- **Returns:** counts of created / already existing / skipped, plus configuration errors (exit code 1).
- **Out of scope:** ad-hoc and on-demand batches (intake processor).

### `batches/intake_processor.py`
- **Purpose:** P4: processes `ComplianceRequestInTake` rows in status `NEW`, oldest first, one per transaction (`FOR UPDATE SKIP LOCKED`).
- **Request types:**
  - `CYCLE_INIT` (D-50): creates the routine batches for the named period. Existing batches are skipped or failed according to `CYCLE_INIT_EXISTING_BATCH` (Q-18).
  - `ADHOC_REQUEST`: creates ad-hoc batches for one source or all effective sources.
    - A source that already has a batch for the period is rejected (`INTAKE_DUPLICATE_PERIOD`, D-35).
    - A period whose extract was triggered is rejected.
    - Adding a source to an untriggered extract depends on `ADHOC_ALLOW_ADD_SOURCE_BEFORE_TRIGGER` (Q-05) and raises the required source count.
  - `CORRECTION_REQUEST`: flags an existing batch for correction (`CORRECTION_FLAGGED`, `DATA_QUALITY_ISSUE_FLAGGED`). No data changes.
- **Result per row:** `PROCESSED`, `PARTIALLY_PROCESSED` or `FAILED` with a reason. Unexpected errors mark only that row `FAILED` and processing continues.
- **Checks:** run type exists and is active; `CYCLE_INIT` needs a ROUTINE run type and `ADHOC_REQUEST` an ADHOC one; at least one effective crosswalk source.
- **Out of scope:** receiving the requests (rows are inserted by users or other systems), loading any data.

---

## 9. File ingest (`ingest/`)

### `ingest/pipeline.py`
- **Purpose:** P5/P6, the end-to-end processing of **one** inbound object (`IngestPipeline.process_file(bucket, key, version_id)`).
- **Steps:**
  1. **Register (C0):** insert a `ComplianceFileLoad` row keyed by bucket/key/version (or ETag).
     - Already terminal → `REPLAY_IGNORED`, and finish any interrupted archive or quarantine move.
     - Non-terminal (crashed earlier) → restart the same `Load_ID`, unless another process holds the batch lock (`IN_PROGRESS`).
  2. **Match:** `TemplateMatcher` against active file configs. Check the file is in the config's inbound folder. Resolve the run type (case rule Q-03). Find the crosswalk row effective on the report start or end date (Q-04). Find the batch for the period.
     - Any failure → quarantine with the matching reason (`FILE_REJECTED_*`).
  3. **Zero-byte check (C1):** an empty file when a header is expected → `FILE_PARSE_ERROR`.
  4. Under the **batch lock**:
     - Download to a temporary folder and compute SHA-256.
     - **Duplicate (C11):** identical to the batch's current load or pending reopen candidate → `FILE_REJECTED_DUPLICATE`. Identical to another batch's file → allowed, `FILE_SAME_CONTENT_OTHER_BATCH` warning (D-52).
     - **Stage** on DATA through the configured engine (delete by `Btch_ID`, then load). Structural problems → quarantine.
     - **Zero records:** rejected unless `Allow_Zero_Rcd_Ind` (D-60).
     - **FILE_LEVEL rules** through the rules adapter (GATE or ANNOTATE from the file config, D-44). A technical rules error → `TechnicalFailure`.
     - **Resolve (§8)** with `resolution_engine.decide` inside one META transaction that locks the CRC row:
       - *Batch open, file passed* (O-1, O-2): swap core on DATA (commits first; replay-safe, D-68), set `NEW_FILE` and the current load, mark the previous load superseded.
       - *Batch open, file failed* (O-3, O-4): batch goes to the exception status; prior promoted data stays current (D-46).
       - *Batch closed, file failed* (X-5): `REOPEN_REJECTED_RULES_FAILED` (D-45).
       - *Batch closed, no active reopen* (X-1): create a reopen override (`LATE_ARRIVAL_REOPEN` if the source was missing, `CORRECTION_REOPEN` if it had data) in `PENDING_REVIEW`.
       - *Batch closed, reopen active* (X-2 – X-4): replace the candidate and reset the review. X-4 keeps the original reopen type (D-47).
     - **Archive** the object (move to the archive path).
  5. After a promotion, call the early-completion hook (extract refresh).
- **Returns:** `IngestOutcome` with result `PROMOTED`, `RULES_FAILED`, `PENDING_APPROVAL`, `REOPEN_REJECTED`, `QUARANTINED`, `REPLAY_IGNORED` or `IN_PROGRESS`, plus ids.
- **Failure handling:** any exception marks the load `FAILED_TECHNICAL` (with sanitised error text) and is re-raised for the orchestrator to retry. A failed object move is logged (`FILE_MOVE_FAILED`) and retried on the next replay.
- **Writes (META):** `ComplianceFileLoad`, `ComplianceRequestControl`, `ComplianceBatchOverride`, audit tables. **Writes (DATA):** staging and core tables.
- **Out of scope:** approving reopens (manual SQL + `process-decisions`), closing batches (trigger only), triggering extracts, S3 event delivery.
- **Design:** §7 P5, §8, §9.4, D-01, D-05, D-37, D-45 – D-47, D-52, D-60, D-68.

### `ingest/resolution_engine.py`
- **Purpose:** the §8 decision tables as a **pure function** (no I/O). `decide(ResolutionInput)` → `ResolutionDecision(action, rule, override_ty)`.

| Batch | File passed? | Prior data / active reopen | Action (rule) |
|---|---|---|---|
| Open | yes | no prior | `PROMOTE` (O-1) |
| Open | yes | prior promoted | `PROMOTE_REPLACE` (O-2) |
| Open | no | no prior | `EXCEPTION_NO_DATA` (O-3) |
| Open | no | prior promoted | `EXCEPTION_KEEP_PRIOR` (O-4) |
| Closed | no | any | `REOPEN_REJECT` (X-5) |
| Closed | yes | none | `REOPEN_INSERT` (X-1) |
| Closed | yes | pending review | `REOPEN_REPLACE_PENDING` (X-2) |
| Closed | yes | approved, not promoted | `REOPEN_RESET_APPROVED` (X-3) |
| Closed | yes | approved and promoted | `REOPEN_RESET_PROMOTED` (X-4) |

### `ingest/file_reader.py`
- **Purpose:** structural reading of files (§9.4 C12 – C14, D-58, D-59). Values are returned as text; PostgreSQL does the typed cast during staging.
- **In scope:**
  - `read_file`: enforces `SUPPORTED_FILE_TYPES` (Q-02) and picks a reader.
  - `read_delimited`: encoding check, trailer removal and optional trailer count check, header skipped (names ignored), **positional** column count must match the staging table, empty → NULL (optional).
  - `scan_delimited`: the same checks, streaming, returning only the row count (Spark engine).
  - `read_with_pandas`: `.xlsx` / `.parquet` (disabled by default).
  - `delimiter_for` (named delimiters such as `PIPE`, `TAB`), `sanitize_db_error` (removes quoted values from database errors so no file content reaches logs).
- **Rejections:** `FILE_TYPE_NOT_SUPPORTED`, `FILE_PARSE_ERROR`, `FILE_COLUMN_COUNT_MISMATCH`, `FILE_TRAILER_COUNT_MISMATCH`.
- **Out of scope:** header name checks (D-59), content rules (GRE), compressed or encrypted files (D-58).

---

## 10. Staging and promotion (`load/`)

### `load/tables.py`
- **Purpose:** target-table introspection through `information_schema` on the DATA connection.
- `columns`; `staging_business_columns` (staging columns minus `btch_id`, `load_id`, `src_file_nm`, `stg_load_dtts`); `core_insert_columns` (staging business columns that exist in core, minus identity, generated and serial columns and `Load_Exclude_Col_List`, D-61).
- Raises `LookupError` if a table or its framework columns are missing.

### `load/engine/base.py`, `load/engine/__init__.py`
- **Purpose:** the engine interface (D-62). `load_to_staging(data_conn, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm, loaded_at, target)` → `StageResult(data_rows, trailer_count)`. `build_engine(engine_cd, settings)` returns the engine for `Engine_Cd` (`PANDAS` or `SPARK`).

### `load/engine/pandas_engine.py`
- **Purpose:** the default single-process engine. Reads with `file_reader`, then in **one** DATA transaction deletes the batch's staging rows and `COPY`s the new rows with the framework columns.
- A type cast failure becomes `FILE_PARSE_ERROR` (sanitised). pandas is needed only for xlsx/parquet.

### `load/engine/spark_engine.py`
- **Purpose:** engine for large delimited files.
- Streams a structural check, reads with Spark (all columns as text), deletes the batch's staging rows through psycopg, then appends via Spark JDBC.
- JDBC URL, user and password come from the target connection unless `SPARK_JDBC_URL` / `SPARK_JDBC_PROPERTIES` override them.
- **Limits:** no trailers, delimited files only. A partial append is never promoted, because promotion checks the staged row count.
- **Status:** written but **not yet run** (PySpark was not installable in the build environment).

### `load/promoter.py`
- **Purpose:** the core swap (D-01, §10.2). Runs inside the caller's DATA transaction while the caller holds the batch lock.
- `swap(...)`:
  1. **Replay check (D-68):** if the batch's only current core rows are exactly this load's expected rows, the swap already happened → return `already_applied`.
  2. Set `current_ind = 0, end_dtts = now` on the batch's current rows.
  3. Insert the load's staging rows as current.
  4. Appended count ≠ staged count → `RowCountMismatch` (the transaction rolls back).
- `staged_row_count(...)`: used before reopen promotion.
- **Out of scope:** updating control tables (callers do), Spark (never used for this step).

### `load/archive_restager.py`
- **Purpose:** D-53. When an approved reopen's staging rows are gone, reload the file from the S3 archive: download, verify SHA-256 against the approved load, stage with the configured engine, and check the row count. Any mismatch or missing object → `TechnicalFailure`.

---

## 11. Manual decisions (`overrides/`)

### `overrides/decision_processor.py`
- **Purpose:** P7/P8. Picks up approvals, rejections and revocations that people made with the Appendix B SQL templates (D-12), validates them, records them, and promotes approved reopens.
- **Step 1, detect:** every override whose `Apprvl_Stat` differs from `Last_Processed_Apprvl_Stat` (row-locked, `SKIP LOCKED`).
  - **Validation:**
    - The transition must be legal: new → pending/approved/rejected, pending → approved/rejected, approved → revoked.
    - Only waivers can be revoked, and waiver decisions are refused once the extract was requested or triggered (D-48).
    - A source waiver cannot be approved on a closed batch.
    - A reopen approval must name `Reviewed_Load_ID = Candidate_Load_ID` (§12.4), and reopen rows must have been created by the framework.
  - **Invalid** → `APPROVAL_INVALID_DETECTED`, no action.
  - **Reopen approved** → `*_REOPEN_APPROVED`, promotion becomes `PENDING`.
  - **Waiver approved, rejected or revoked** → audit event, extract queued for refresh.
  - **Reopen rejected** → candidate load marked `SUPERSEDED`.
- **Step 2, promote:** each approved reopen with promotion `PENDING` or `FAILED`, under the batch lock:
  1. Re-check the approval still matches the candidate.
  2. Re-stage from the archive if staging rows are missing (`REOPEN_RESTAGED_FROM_ARCHIVE`).
  3. Swap core on DATA (replay-safe), then in META: supersede the prior load, set the batch to complete with `NEW_FILE`, mark the load and override `PROMOTED`, write `REOPEN_PROMOTED`, and clear the correction flag for a correction reopen.
  4. On any error: promotion `FAILED`, `REOPEN_PROMOTION_FAILED`, retried on the next run.
- **Step 3, follow-up:** refresh extracts affected by waiver decisions; for reopened extracts call the re-trigger hook (D-41).
- **Returns:** counts and ids (processed, invalid, promoted, promotion failed). Exit code 1 if anything was invalid or failed.
- **Out of scope:** creating reopen overrides (pipeline), creating waivers (manual SQL), the approval UI (later phase).
- **Design:** §7 P7/P8, §12.4, D-04, D-12, D-41, D-48, D-53, D-64.

---

## 12. Rules engine adapter (`validation/`)

### `validation/gre_adapter.py`
- **Purpose:** the single interface to the UMcM Generic Rules Engine (D-08, D-43, D-44, D-63).
- `RuleEngine.run(data_conn, metadata_conn, bindings, run_params, mode)` → `RuleOutcome(status, failed_rules, warned_rules, error)`.
  - **Status values:** `PASSED`, `PASSED_WITH_WARNINGS`, `FAILED`, `ERROR`.
- **Implementations:**
  - `CallableRuleEngine`: calls a function once per bound rule group and variant and applies the mode (GATE failures → `FAILED`, ANNOTATE failures → warnings). Any exception or malformed result → `ERROR` (technical, never a data failure).
  - `GreRuleEngine`: loads that function from `GRE_ENTRYPOINT` (`module:function`). Not configured → every run with bindings returns `ERROR` (Q-12).
  - `NoRulesEngine` (`RULE_ENGINE=none`): always passes.
  - `build_rule_engine`: also accepts `module:Class` for a custom engine.
- **Entry-point contract:** `fn(data_conn, metadata_conn, rule_group, rule_variant, run_params) -> [{"rule_ref", "passed", "detail"}]`.
- **Out of scope:** the rules themselves and how GRE stores results (Q-12, Q-19). Choosing the mode is the caller's job: FILE_LEVEL from the file config, PERIOD_LEVEL from the extract policy.

---

## 13. Extracts (`extract/`)

### `extract/eligibility.py`
- **Purpose:** pure function `compute(EligibilityInput, strict_waiver_auto)` → `AUTO`, `MANUAL_ONLY` or `NOT_ELIGIBLE`, with a reason and warnings (§11.1 – §11.2).
- **Order of checks:**
  1. Before `Earliest_Trigger_Dt` (SLA hold, D-38) → not eligible.
  2. No required sources, period rules not yet evaluated, or a period rules error → not eligible.
  3. All sources received and period rules clean → `AUTO`.
  4. **STRICT_ALL_PASS:** complete and clean only through approved waivers → `MANUAL_ONLY` (or `AUTO` if `STRICT_WAIVER_AUTO_TRIGGER`, Q-06); otherwise not eligible with the reasons.
  5. **BEST_EFFORT:** `MANUAL_ONLY` with warnings for missing sources, no data at all (allowed, D-49) or failed period rules.

### `extract/control.py`
- **Purpose:** P9. `ExtractControlService.refresh(extract_id, trigger_cd)` recounts an extract, runs combine and period rules when needed, and records eligibility. Runs under the `EXT` lock (`refresh_locked` for callers that already hold it).
- **Steps, in one META transaction:**
  1. Load the extract and its policy (gating mode, period rules mode).
  2. Count sources received (`NEW_FILE`) and approved source waivers; set `COMPLETE` / `PARTIAL` / `PENDING` and the included / missing / waived source lists.
  3. Compute a data signature from (batch, current load) pairs.
  4. **Combine and period rules** (§10.4):
     - Run when the reason requires it and the data or rule result is not already fresh. An early completion runs only once every source has data; a manual refresh always runs.
     - Rules run on DATA with the batch and load id lists (`PERIOD_RULES_FAILED` or `RULES_ENGINE_TECHNICAL_FAILURE` on problems).
     - If the data changed without a combine, the rule result becomes `PENDING`.
  5. **Re-trigger flag:** if the data changed after a successful trigger, set `Retrigger_Required_Ind` (`EXTRACT_RETRIGGER_REQUIRED`).
  6. Compute eligibility in the business timezone; log `EXTRACT_ELIGIBILITY_CHANGED` when it changes; update the extract row.
- `combined_rows(conns, extract)`: the current core rows of every received batch, filtered by batch **and** current load, read from DATA. For inspection and tests; extract generation itself is outside the framework (D-39).
- **Out of scope:** calling the extract job, closing batches.

### `extract/trigger.py`
- **Purpose:** P11/P12, the only place a batch closes (D-39 – D-42).
- **`fire(extract_id, trigger_ty, requested_by, ack_warnings)`**, where `trigger_ty` is `AUTO`, `MANUAL` or `RETRIGGER`, under the `EXT` lock:
  1. Refresh the extract.
  2. **Check it is allowed** (otherwise `EXTRACT_TRIGGER_BLOCKED` + `TriggerBlocked`):
     - no call already in flight;
     - `AUTO` / `RETRIGGER` need `AUTO` eligibility;
     - `MANUAL` needs at least `MANUAL_ONLY` and acknowledged warnings;
     - `RETRIGGER` needs the re-trigger flag;
     - an already triggered extract with unchanged data is refused.
  3. Take every batch lock without waiting; a busy batch → `BATCH_CLOSE_DEFERRED_LOCKED` + `TriggerDeferred`.
  4. Insert `ComplianceExtractTrigger`, render the job parameters, set the extract to `REQUESTED` (`EXTRACT_TRIGGER_REQUESTED`), all committed **before** the call.
  5. Call the connector, retrying with exponential backoff up to `Max_Call_Retry_Cnt` (Q-07).
     - **Accepted** → `complete()`.
     - **Unknown outcome** → `EXTRACT_TRIGGER_RECONCILE_REQUIRED` + `TriggerOutcomeUnknown`.
     - **Rejected** → `fail()` + `TriggerCallFailed`.
- **`complete(trigger_id, ...)`:** marks the call succeeded, sets the extract `TRIGGERED` and closed, stores the triggered data signature, clears the re-trigger flag, and closes every open batch of the extract:
  - batch in exception → complete-with-exception;
  - batch with a promoted file → complete;
  - otherwise → not provided / `MISSING` (`SOURCE_MISSING_AT_CLOSE`).
  - Writes `BATCH_CLOSED` per batch and `EXTRACT_TRIGGERED` (or `..._WITH_WARNINGS`).
- **`fail(...)`:** marks the call and the extract `FAILED` (`EXTRACT_TRIGGER_FAILED`). Batches stay open.
- **`reconcile()`:** `REQUESTED` calls older than `TRIGGER_RECONCILE_MINUTES` whose extract lock is free are completed if a job reference exists, otherwise flagged for manual resolution.
- **`resolve(trigger_id, accepted, actor, job_run_ref)`:** manual resolution (`resolve-trigger`).
- **`render_params(...)`:** `LITERAL`, `EXTRACT_ATTR` (allowed attributes in `EXTRACT_ATTRS`; dates use `PARAM_DATE_FORMAT`), `BTCH_ID_LIST`, `LOAD_ID_LIST`, `TRIGGER_ID` (D-42).
- **Out of scope:** generating the extract, tracking the extract job's own result (D-39).

### `extract/evaluator.py`
- **Purpose:** P10, the `evaluate-extracts` sweep.
- **Steps:**
  1. `trigger.reconcile()`.
  2. Select extracts past (or within a day of) their hold that are not triggered, failed (if `RETRY_FAILED_TRIGGERS_ON_SWEEP`, Q-07), or flagged for re-trigger (if `AUTO_RETRIGGER_AFTER_REOPEN`, Q-16).
  3. Attempt `AUTO` or `RETRIGGER` for each. Blocked extracts are counted silently; deferred, failed and unknown outcomes are listed.
- **`after_reopen_promotion(extract_id)`:** D-41. Refresh, then re-trigger automatically if the extract was triggered before, is flagged, and re-trigger is enabled.
- **Out of scope:** manual triggers.

### `extract/connectors/`
- **`base.py`:** `ExtractConnector.call(policy, params)` → `CallResult(accepted, job_run_ref, response_txt, ambiguous)`. The framework only needs to know whether the call was **accepted**.
- **`glue_job.py`:** `StartJobRun` with the parameters as job arguments.
  - Refused or never sent → not accepted.
  - Any other error (for example a timeout after sending) → ambiguous.
- **`http_api.py`:** sends a JSON object to `Endpoint_Url` with the configured method and timeout. Accepted status ranges come from `HTTP_ACCEPTED_STATUS` (Q-08). An optional auth header comes from a Secrets Manager secret (provisional, Q-08).
  - Connection refused → not accepted; timeout → ambiguous.
  - The job reference comes from the `x-request-id` header or a known JSON field.
- **`__init__.py`:** `build_connector(job_ty, settings)` for `GLUE_JOB` and `HTTP_API`.

---

## 14. Notifications and storage

### `notify/notifier.py`
- **Purpose:** P13, send notifications for audit events (D-54).
- `NotificationDispatcher.run()`:
  1. Read unsent events whose type is flagged for notification.
  2. Pick the channel (file config, else event type default) and recipients (failure or success list from the file config, else `DEFAULT_NOTIFY_EMAILS`).
  3. Build a subject and a body of ids and codes only (no file content).
  4. Send, then set `Notified_Ind = 1`.
  - A failed send is logged and retried next run; it never rolls back pipeline state.
- **Channels:** `LogChannel` (default) and `AwsChannel` (SES email and/or SNS), selected by `NOTIFY_BACKEND`.

### `storage/object_store.py`
- **Purpose:** object storage abstraction.
- **`ObjectStore` interface:** `head`, `exists`, `download`, `copy`, `delete`, and `move` (copy to `<dest>/<sub_prefix><name>` then delete).
- **Implementations:** `S3ObjectStore` (boto3, versions and ETags); `LocalObjectStore` (`<root>/<bucket>/<key>`, for development and tests).
- **Helpers:** `parse_uri`, `basename`, `dirname`, `sha256_file`. `build_object_store(settings)` selects by `OBJECT_STORE`.
- **Out of scope:** S3 event delivery and bucket policies.

---

## 15. SQL files (`sql/`)

| File | Purpose |
|---|---|
| `ddl/001_schema.sql` | All framework tables, constraints, indexes and the `vw_crc_current_flags` view. Schema-unqualified; applied by `init-db` into the metadata schema. Source of truth for Appendix A. |
| `seed/010_event_types.sql` | Event vocabulary: target audit table, category, default severity, notify flag. Re-runnable. |
| `seed/020_period_strategies.sql` | Strategy codes → SQL file and required parameters. Re-runnable. |
| `seed/030_request_status_PROVISIONAL.sql` | **Provisional** `Req_Stat` codes and transitions mapped to the abstract states. Replace when Q-01 is answered; skip with `init-db --no-provisional-status`. |
| `period_strategies/*.sql` | One `SELECT` per strategy returning `rpt_start`, `rpt_end` from `%(sched_dt)s` (and lookback parameters): same day, previous day, previous N days, same weekday N weeks back, previous calendar week, current and previous calendar month, rolling month, previous quarter, previous year. |
| `templates/approvals.sql` | Appendix B templates for approvals, rejections, waivers and revocations. Run as `framework_approver` with `search_path` set to the metadata schema; each must report 1 row. |

---

## 16. Tests (`tests/`)

| File | Covers |
|---|---|
| `conftest.py` | Database fixtures: recreates the metadata schema (`TEST_METADATA_SCHEMA`) and the sample `stg_t` / `core_t` tables; with `TEST_DATA_DATABASE_URL` the tables go to a second database registered as connection `DATA1`. |
| `helpers.py` | Synthetic configuration seed, fake rules engine and connector, `make_app`, file builders, approval helper. |
| `test_templates.py` | Template grammar, matching, ambiguity, invalid tokens, case sensitivity. |
| `test_resolution_engine.py` | Every §8 row. |
| `test_eligibility.py` | Every §11.2 case, including waivers and the SLA hold. |
| `test_btch_id.py` | `Btch_ID`, `Seq`, SLA close dates. |
| `test_period_and_scheduler.py` | Every period strategy (month ends, leap day, quarter and year boundaries), missing strategy parameters, one batch per period, catch-up (scheduled period, actual creation date), effective windows and go-live date, no batch added to a triggered extract. |
| `test_intake.py` | CYCLE_INIT, ADHOC (duplicates, triggered periods), CORRECTION. |
| `test_ingest.py` | Promotion, replacement, quarantine reasons, duplicates, zero records, rules GATE/ANNOTATE, technical failure and replay, cross-database commit-failure replay. |
| `test_reopen.py` | Late-arrival and correction reopens, candidate replacement, rejection, archive re-staging, promotion failure, invalid manual changes. |
| `test_extract.py` | Auto trigger after the hold and batch close, STRICT with source and rule waivers, BEST_EFFORT acknowledgement and zero data, rejected calls and retries, unknown outcomes and `resolve-trigger`, deferral on a locked batch, period rules technical error. |
| `test_validator_notify_cli.py` | Validator checks (including connection and settings checks), notifications, CLI end to end, rules adapter contract, HTTP and Glue connectors. |
| `test_readers_and_store.py` | xlsx reader, delimiters, encoding and streaming scan, local object store, Spark engine prerequisites (xlsx and Spark tests skip when their libraries are missing). |
| `test_connections_settings.py` | Settings precedence and conversion, config file lookup, connection precedence for META and named connections, connection manager, start-up with metadata settings. |

---

## 17. Who writes which table

All tables are in META. Staging and core tables are in DATA.

| Table | Written by |
|---|---|
| `ComplianceRequestControl` (batches) | `batches/crc_repository` (create), `ingest/pipeline` (status, current load), `overrides/decision_processor` (reopen promotion), `extract/trigger` (close) |
| `ComplianceExtractControl` | `batches/crc_repository` (create, hold date), `batches/intake_processor` (required count), `extract/control` (counts, rules, eligibility), `extract/trigger` (trigger status, close) |
| `ComplianceExtractTrigger` | `extract/trigger` |
| `ComplianceFileLoad` | `ingest/pipeline`, `overrides/decision_processor` (promoted, superseded) |
| `ComplianceBatchOverride` | `ingest/pipeline` (reopen rows), `overrides/decision_processor` (processing state, promotion); people via `templates/approvals.sql` (decisions, waivers) |
| `ComplianceRequestInTake` | people or upstream systems (new rows); `batches/intake_processor` (result) |
| `ComplianceRequestFileDetail`, `CMS_ComplianceExceptionsAudit` | `audit/event_logger` only (`notify/notifier` sets `Notified_Ind`) |
| `ComplianceFrameworkSetting` | `db.init_db` (default rows); people via SQL (values) |
| Configuration tables, `ComplianceDbConnection` | people via SQL; `db.init_db` seeds event types, period strategies and provisional statuses |
| Staging tables (DATA) | `load/engine/*` (delete by `Btch_ID` + load) |
| Core tables (DATA) | `load/promoter` only |
