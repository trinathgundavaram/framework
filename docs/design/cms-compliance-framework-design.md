# CMS Compliance Framework: Consolidated Design (v6)

| | |
|---|---|
| **Status** | Build-ready for every module that §16 (Open Questions) does not name as blocked. |
| **Revision** | **v5, 2026-09-17: simpler control tables (D-74 – D-79).** The framework no longer calls the extract job: it **closes the run** when the data is complete, and the project's job chain generates the extract afterwards (D-76). `ComplianceExtractTrigger` is gone, and with it waivers, reopen candidates and revocation. One override shape covers `REUSE`, `LATE_ARRIVAL` and `CORRECTION`, each approved with a validity date (D-74). `Earliest_Close_Dt`, `Intake_ID`, `Current_Load_ID` and `Closed_By_Trigger_ID` are removed from the request control table; the SLA hold is computed from the run date and `SLA_Days` (D-77), and a batch's current data is its single `PROMOTED` load (D-75). The batch and extract grain now include the run date, so daily runs of one report period each get their own batch and extract (D-77). Intake is ad-hoc only and carries a request window (D-79). **v4, 2026-09-17: simplification (D-69 – D-73).** Six configuration tables removed (status, period strategy, DB connection, framework setting, extract policy, extract job parameter); connections, settings, report periods and extract jobs are job-level; one database for metadata, staging and core; crosswalk and file config trimmed; carry-forward reintroduced as a per-batch approval for run types that allow it (D-70); the package collapsed into 15 modules. v3.2, 2026-09-16: configurable metadata database and schema, named data-database connections, metadata-driven runtime settings (D-65 – D-68). v3.1, 2026-09-16 (implementation notes added: Appendix A columns, extra operational events; code in `src/framework`). v3, 2026-09-16. Replaces v2 and v1. v3 is **project-agnostic** and **filename-driven**. Carry-forward is removed, batches are keyed by report period, and batches close only when the framework triggers the extract. See §17. |
| **Basis** | Only the decisions recorded in §2. Earlier worked examples, mock data and legacy/sample code are deliberately **not** used as inputs. |
| **Conventions** | `D-nn` = confirmed decision. `Q-nn` = open question (§16). **⚠** = depends on an open question. |

## Contents
1. Purpose & Scope
2. Decision Register
3. Architecture & Build Phasing
4. Core Concepts & Glossary
5. Data Model
6. State Machines
7. Process Flows (P1–P13)
8. File-Resolution Decision Tables
9. Filename Templates & Matching
10. Staging, Promotion & Combine Mechanics
11. Run Close
12. Idempotency & Concurrency
13. Edge Cases & Negative Scenarios
14. Feasibility, Drawbacks & Risks
15. Package Structure, Tests & Operations
16. Open Questions
17. Change Log
- Appendix A: PostgreSQL DDL (tested on PostgreSQL 16)
- Appendix B: Override SQL Templates
- Appendix C: Synthetic Walkthrough

---

## 1. Purpose & Scope

A single, **project-agnostic** framework for compliance source files. It does the following:
- Registers expected batches.
- Recognises incoming files purely from **filename templates stored in config**.
- Validates, stages and promotes the data to core.
- Handles late arrivals, corrections and data reuse through human approval (D-74).
- Runs cross-source period validation.
- **Closes the run** when its data is complete; the project's job chain generates the extract afterwards.

**Adding a project, table, source or run type is config only** (D-27). The package has no project-specific code paths, names or branches. Scheduling a project is job configuration, not code: the report period is a named SQL statement (`period_sql.py`, or a project file passed with `--period-file`), and the extract job, its parameters and the gating mode are arguments of the project's job (D-71). A new extract-job *type* (the connector) still needs a package deploy.

**In scope:** config and validation; batch creation (scheduled, re-run for a missed date, ad-hoc intake); file intake (template match, dedupe, quarantine); staging; file-level validation (UMcM GRE); core promotion; manual overrides (reuse, late arrival, correction); combine and period-level validation; extract eligibility; **closing the run** and its batches; audit and notifications.

**Out of scope:**
- Generating the extract itself (D-39, D-76). The framework closes the run and records which batches it contains; the project's job chain generates the submission and the framework does not track it.
- An approval UI (D-12).
- AWS orchestration wiring, which is deferred (D-13).

---

## 2. Decision Register

### 2.1 Active decisions

| ID | Decision |
|---|---|
| D-01 | **Promotion (core load).** In one transaction, set `Current_Ind = 0` on the current core rows with the same `Btch_ID`, then append the newly staged rows. |
| D-02 | **Override keys (v5).** At most one active (`PENDING_REVIEW` or `APPROVED`) override per (`Req_ID`, `Override_Ty`). A batch may therefore hold a `REUSE` and a later `CORRECTION` row, but never two of a kind. |
| D-03 | **Batch and load identity.** `Btch_ID` is fixed at batch creation and never changes. Every physical file gets its own `Load_ID`, which is stamped everywhere its data or events appear. |
| D-04 | **Closed batches stay closed.** A closed batch that receives a file under an approved override stays closed (`Batch_Close_Ind = 1`); the promotion runs under that approval (D-74). |
| D-05 | **Staging.** Staging is never truncated. Before loading, the framework deletes only `WHERE Btch_ID = :btch_id`. |
| D-08 | **Rules engine.** File-level and period-level validation use UMcM GRE. |
| D-09 | **Extract grain includes `Run_Ty`.** |
| ~~D-11~~ | *Withdrawn in v5.* Waivers are removed. A STRICT run that cannot complete is closed by a person with `close-extract` under `BEST_EFFORT`, which records the warnings on the extract row. |
| D-12 | **Approvals.** Overrides are created, approved and rejected with manual SQL for now (`sql/approvals.sql`); a UI comes later. |
| D-13 | **AWS orchestration** (Glue triggers vs Step Functions) is decided later. The package is orchestration-agnostic. |
| D-14 | **Replacement while open.** A new file for an open batch replaces the previous one: same `Btch_ID`, new `Load_ID`, no approval. |
| D-15 | **No batch for the period.** A file whose (project, table, source, run type, report period) has no batch is quarantined immediately. |
| D-17 | **Independence.** The framework runs independently of existing stores and processes; existing processes migrate onto it. Column names are framework-owned. |
| D-19 | **GATE failures.** A file-level GATE failure rejects the whole file. |
| D-21 | **Early combine.** Combine and period rules also run as soon as every required source has data. |
| D-25 | **`Table_Nm`** is the physical core table name. |
| D-26 | **One file per batch.** One file per source per batch; any sub-type split lives inside the data. |
| D-27 | **Project-agnostic.** All behaviour comes from config tables; the code has no project-specific logic. |
| D-28 | **Filename templates.** Each file-config row stores a filename **template** with placeholders `{RUNTY}`, `{RPTSTART}`, `{RPTEND}`, `{TS}` and literal text; the project, table and source are written literally (v6, §9). |
| D-29 | **`Req_Dt_Key`** is the **actual date the batch was created**. It is embedded in `Btch_ID` and is **not** in filenames. A batch created late (job re-run with `--as-of`) uses the date of that run. |
| D-29b | **Late batches.** A missed scheduled run is recreated by running `run --module BATCH_CREATION --as-of <missed date>`; the report period is computed from that date (D-71). |
| D-30 | **One batch per period.** Exactly **one batch per (Project, Table, Source, Run type, Report start, Report end)**, including ADHOC. This is the CRC grain. |
| D-31 | **Filename tokens.** The project, table and source are literal text in each config row's template (v6; the alias columns are removed). `{RUNTY}` must equal a `Run_Ty` code exactly. |
| D-32 | **Date format.** Filename dates are `YYYYMMDD`. |
| D-33 | **One file-config row per (Project, Table, Source).** `{RUNTY}` selects the run type, which must be configured for that source. |
| D-34 | **No automatic carry-forward.** A batch without usable data at close is `MISSING`, unless a carry-forward was approved for it (D-70). |
| D-35 | **Duplicate ad-hoc intake.** A second ADHOC intake for a (table, source, period, run date) that already has a batch creates nothing; the existing batch is reused. |
| D-36 | **`{TS}`.** Filenames carry a unique token `{TS}` = `YYYYMMDDHHMMSS`. |
| D-37 | **Latest arrival wins.** `{TS}` only makes names unique and is not used for ordering. |
| D-38 | **SLA hold.** `SLA_Days` is a **minimum hold** before a batch may close. A batch may close from the start of `Req_Dt_Key + (SLA_Days − 1)` calendar days: SLA 1 = the creation day, SLA 2 = any time the next day, and so on. |
| D-39 | **Closing a batch (v5).** Batches close **only** when the run is closed — automatically by `evaluate-extracts` once the run is fully eligible, or by a person with `close-extract`. Closing resolves every batch of the run and freezes the batch list on the extract row. Extract generation itself is outside the framework (D-76). |
| D-40 | **Closing the run (v5).** The close is **automatic** when the run is fully eligible (all sources have data, period rules passed, SLA hold over). It is **manual** (`close-extract`) otherwise. **BEST_EFFORT:** a manual close is allowed any time after the SLA hold, with acknowledged warnings. **STRICT_ALL_PASS:** only a complete, clean run may close. |
| D-41 | **Regeneration after a late arrival or correction (v5).** When a file is promoted into a closed batch, combine and period rules re-run and `Regenerate_Required_Ind = 1` marks the run for regeneration. The project's job chain reads that flag; the framework never calls the extract job. |
| ~~D-42~~ | *Withdrawn in v5 (D-76).* The extract job and its parameters belong to the project's job chain, not to the framework. |
| D-43 | **GRE location.** GRE metadata and results live in the **same Postgres database** as the framework. |
| D-44 | **GATE/ANNOTATE** for file rules is a job setting (`FILE_RULES_MODE`, D-73). File rules run whenever the source has FILE_LEVEL rule bindings. |
| D-45 | **A file for a closed batch fails GATE.** The file is rejected (`RULES_FAILED`); the override is untouched; an alert is raised. |
| D-46 | **Replacement fails GATE while open.** The prior promoted data stays current; the batch goes to `EXCEPTION_PENDING`. |
| D-47 | **Second correction (v5).** Every promotion into a closed batch needs the override type that matches the batch's state at that moment: `LATE_ARRIVAL` while it has no data, `CORRECTION` once it has. A second correction therefore uses the `CORRECTION` row, which stays usable until its `Valid_Thru_Dt_Key`. |
| ~~D-48~~ | *Withdrawn in v5 (D-74).* Waivers and revocation are gone; an approval simply runs out on `Valid_Thru_Dt_Key`. |
| D-49 | **BEST_EFFORT with no data.** No minimum-data requirement; a zero-data trigger is allowed with a warning. |
| ~~D-50~~ | *Withdrawn in v5 (D-79).* Routine batches come only from the scheduled `BATCH_CREATION` job; intake is ad-hoc only. |
| D-51 | **`Cmplnc_Vrsn`.** An attribute on **effective-dated** crosswalk rows. A new version end-dates the old row and adds a new one. |
| D-52 | **Same content, different batch.** Content identical to another batch's file is allowed and logged as a warning. |
| ~~D-53~~ | *Withdrawn in v5.* A file is staged and promoted in one pipeline run, so staged rows cannot go missing between two jobs; re-staging from the archive is removed. A file quarantined because every batch was closed is simply re-delivered once the override exists (D-78). |
| D-54 | **Notifications (v6).** Every notification is an email (SES; `NOTIFY_BACKEND=log` only logs). Which events are emailed is fixed in `audit.AUDIT_EVENTS`; recipients come from the file config's success / failure lists or `DEFAULT_NOTIFY_EMAILS`. |
| D-55 | **Database.** RDS PostgreSQL (version ⚠ Q-11), direct connections (no RDS Proxy). |
| D-56 | **Correction flags do not age.** No aging alert. |
| D-57 | **Retention.** Keep everything; partition large tables by month. |
| D-58 | **File wrappers.** Plain files only: no compression, encryption or control files. |
| D-59 | **Column mapping.** By **position**. The data column count must equal the staging business-column count, otherwise `FILE_PARSE_ERROR`. Header names are **not** checked. |
| D-60 | **Zero-record files.** Allowed or rejected per file-config row (`Allow_Zero_Rcd_Ind`). |
| D-61 | **Columns not loaded.** Identity, generated and serial core columns are skipped automatically. (v4: the per-config exclude list was removed.) |
| D-62 | **Engine.** File volumes are mixed; the engine (pandas / Spark) is a job setting (`LOAD_ENGINE`, D-73). |
| D-63 | **Period-level validation mode.** Job setting `PERIOD_RULES_MODE` (was `Period_Rules_Vld_Md` on the removed extract policy). |
| D-64 | **Approver role.** Approvals run under a dedicated `framework_approver` DB role limited to the override table. *(You had no preference; this is the design choice.)* |
| D-65 | **One database, configurable schema.** Config, control, audit, staging and core tables live in **one PostgreSQL database**; the framework tables use a configurable schema (`FRAMEWORK_METADATA_SCHEMA`, default `cms_compliance`), the staging/core tables the schemas named in the file config. *(v4 replaces v3.2's metadata/data database split.)* |
| D-66 | **Connection from `.env` or Secrets Manager (v4).** Locally the database comes from `.env` (`FRAMEWORK_DB_DSN` or `FRAMEWORK_DB_*`); in AWS from the Secrets Manager secret named by `FRAMEWORK_DB_SECRET_NAME`. Explicit values override the secret. There is no connection table. |
| D-67 | **Settings are not stored in tables (v4).** Precedence: job argument (`--set NAME=VALUE`) > environment (`FRAMEWORK_<NAME>`) > `.env` > built-in default. `ComplianceFrameworkSetting` is removed. |
| D-68 | **Promotion is one transaction (v4).** Because staging, core and control share one database, the core swap, the CRC update and the audit rows commit or roll back together. *(Replaces v3.2's cross-database replay rule.)* |
| D-69 | **Fixed `Req_Stat` list (v4, answers Q-01 for now).** `PENDING`, `PROMOTED`, `CARRIED_FORWARD`, `EXCEPTION_PENDING` (open); `COMPLETED`, `COMPLETED_WITH_EXCEPTION`, `DATA_NOT_PROVIDED` (closed). The values and the legal transitions are in code (`common.TRANSITIONS`; no CHECK constraint since v6). The status and transition tables are removed. |
| D-70 | **Carry-forward by approval (v4, `REUSE` in v5).** `ComplianceRunType.Carry_Fwd_Ind = 1` allows an **open batch without data** to reuse the data of the latest earlier **closed batch with data** of the same (project, table, source, run type), after a manual `REUSE` override is approved (optionally naming `Reuse_Btch_ID`). The batch becomes `CARRIED_FORWARD` / `Resolution_Ty = CARRY_FORWARD`, counts as received, and the combine reads the reused batch's current core rows (no data is copied). A file arriving before close replaces it; when the approval runs out (`Valid_Thru_Dt_Key`), `process-decisions` returns the open batch to `PENDING`; a file after close needs a `LATE_ARRIVAL` override. |
| D-71 | **Report period is a job parameter (v4).** The crosswalk no longer holds period strategy, lookback, cron or time zone. The project's scheduled job runs `run --module BATCH_CREATION --project --run-type --period <NAME>`; `<NAME>` is a statement in `period_sql.py` (or in a project `.py` file passed with `--period-file`). The run date is today in `BUSINESS_TZ` or `--as-of`. `CompliancePeriodStrategy`, cron expansion and the `catchup` command are removed. |
| D-72 | **Gating at job level (v4, trimmed in v5).** `ComplianceExtractPolicy` and `ComplianceExtractJobParam` are removed. The project's `evaluate-extracts` job carries `EXTRACT_GATING_MODE`, `PERIOD_RULES_MODE` and `AUTO_CLOSE_EXTRACTS`. The extract job settings themselves are gone with D-76. |
| D-74 | **One override shape, three types (v5).** `ComplianceBatchOverride` holds `REUSE`, `LATE_ARRIVAL` and `CORRECTION` rows with the same columns: the batch grain, an optional `Reuse_Btch_ID`, a reason, the approval fields and `Valid_Thru_Dt_Key`. Rows are written and approved by hand (D-12). **There is no revoke:** to stop an override, move its validity date into the past. `LATE_ARRIVAL` (closed batch with no data) and `CORRECTION` (closed batch with data) are read by the ingest pipeline; `REUSE` is applied and expired by `process-decisions`. Candidate/reviewed loads, promotion status and the decision watermark are gone: a decision is made on the batch, not on a particular file. |
| D-75 | **No `Current_Load_ID` (v5).** A batch's current data is its single `ComplianceFileLoad` row with `Load_Stat = 'PROMOTED'` (partial unique index `ux_fileload_promoted`). A promotion supersedes the previous row **before** it promotes the new one, inside the same transaction. |
| D-76 | **The framework closes the run; it does not generate or call the extract (v5).** `ComplianceExtractTrigger`, the Glue/HTTP connectors and every `EXTRACT_JOB_*` / `EXTRACT_PARAMS` setting are removed. The closed extract row carries `Combine_Btch_ID_List`, `Closed_Data_Signature` and `Regenerate_Required_Ind`; the project's job chain reads them and generates the submission. |
| D-77 | **Run date in the grain, SLA computed (v5).** The CRC and extract grain include `Req_Dt_Key`, so the same report period run daily for ten days gets ten batches and ten extracts, one per run date. The SLA hold is `Req_Dt_Key + (SLA_Days − 1)`, computed from the run type whenever it is needed and **never stored** (`Earliest_Close_Dt` and `Earliest_Trigger_Dt` are removed). |
| D-78 | **Batch selection for an arriving file (v5).** A filename carries the report period but not the run date, so a file is matched to the **open** batch of its grain with the **latest run date ≤ today**. If every batch of that grain is closed, the most recent one is used and the file is promoted only under an approved, still-valid override; otherwise it is quarantined with `FILE_REJECTED_BATCH_CLOSED`, which is a **retryable** quarantine: re-delivering the same object after the override exists reprocesses the same `Load_ID`. |
| D-79 | **Intake is ad-hoc only, with a request window (v5).** `ComplianceRequestInTake` is used by ADHOC run types. `Req_Start_Dt_Key` / `Req_End_Dt_Key` say how long batches must be created for the same report period: one batch per run date from the start date through the end date (equal dates = a one-off). v6: no status, request type, reason or error columns — a request is due while its window is open, `Last_Run_Dt_Key` records the last run date handled, and outcomes are audit events under the `Intake_ID`. Whether a batch was scheduled or requested follows from its run type's category. |
| D-73 | **Leaner configuration rows (v4).** The crosswalk only says which (project, table, source, run type) apply and when (plus `Cmplnc_Vrsn`). The file config keeps the file contract, locations, targets and notification recipients; `Target_Connection_Nm`, `Engine_Cd`, `Rules_Vld_Md`, `Is_Rules_Engine_Required`, `Load_Exclude_Col_List` and `Sns_Topic_Arn` are removed. |

Also carried from v1: gating modes `STRICT_ALL_PASS` / `BEST_EFFORT` (a job setting since v4); extract generation is external; notification recipient lists are comma-delimited text; the local package is built first.

### 2.2 Withdrawn
All of these were withdrawn by D-34, D-30 or D-39:
- **v2 carry-forward anchors (D-34):** D-06 (revoke → candidate), D-18 (`Used_Btch_ID` = anchor), D-22 (carry-forward vs reopen), D-24 (anchor expiry). D-23 (carry-forward counts as received) returns in D-70 for approved carry-forwards only.
- **v3.2 configuration (v4):** the metadata/data database split, `ComplianceDbConnection`, `ComplianceFrameworkSetting`, `framework.ini`, cross-database promotion replay.
- **Grain change (D-30):** D-10 (`Intake_ID` in the grain).
- **v5 removals:** `ComplianceExtractTrigger` and the extract connectors (D-76), waivers (D-11, D-48), reopen candidates and `Promotion_Stat` (D-74), `CYCLE_INIT` (D-50), archive re-staging (D-53), revocation of any override (D-74).
- **Close change (D-39 / D-38):** D-16 (SLA closes batches).
- **Minimum data (D-49):** D-20 (BEST_EFFORT needs ≥1 source).
- **Filename rules (D-28–D-37):** D-07 (filename tokens, now superseded).

---

## 3. Architecture & Build Phasing

**One package, thin callers.** The `framework/` package holds all logic and has no AWS-orchestration imports. Entry points only parse arguments, call one service and set the exit code. Every time-based command takes `--as-of`; nothing inside calls "today" directly.

**Phase 1 (build now, local: pytest + PostgreSQL, Docker optional):**

| CLI command | Service | Eventual trigger (D-13) |
|---|---|---|
| `init-db` | `db.init_db` (schema once) | deploy |
| `show-config` / `test-connection` | `settings` (D-66, D-67) | deploy / ops |
| `validate-config` | `config.validate_all` | CI, and before any config change is applied |
| `run --module BATCH_CREATION --project [--run-type --period] [--as-of]` | `batches.create_batches` (ROUTINE) + `batches.IntakeProcessor` (ad-hoc request windows, D-79) | project schedule (D-71) |
| `run --module FILE_LOAD --bucket --key [--version-id]` | `ingest.IngestPipeline.process_file` | S3 event |
| `run --module FILE_LOAD [--bucket --prefix]` | `ingest.IngestPipeline.process_path` (every object at one location, or at every configured location) | poll, or a bulk drop |
| `process-decisions` | `overrides.DecisionProcessor` | poll (every few minutes) |
| `evaluate-extracts [--project]` | `extract.ExtractEvaluator` (refresh + automatic close sweep) | project schedule, every 15 min (D-72) |
| `close-extract --extract-id --closed-by [--ack-warnings]` | `extract.ExtractControlService.close` | human |
| `refresh-extract --extract-id` | `extract.ExtractControlService.refresh` | chained / human |

Every command takes `--set NAME=VALUE` job arguments, so one job definition per project carries that project's period and gating mode. Generating the extract is the next step in the project's own job chain, after `evaluate-extracts` or `close-extract` (D-76).

**Phase 2 (deferred):** wrap the same services in Glue or Step Functions. Constraints for Phase 2:
- The file pipeline needs a dedicated DB session for its whole run, because it holds session advisory locks (§12).
- Approvals are detected by polling.
- S3 bursts must be queued beyond the job concurrency limit.

---

## 4. Core Concepts & Glossary

| Term | Definition |
|---|---|
| **Batch (CRC row)** | One per `(Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key)` (D-30, D-77). Current-state; updated in place; never duplicated. |
| **Report period** | `Rpt_Start_Dt_Key..Rpt_End_Dt_Key`. Scheduled batches get it from the named period SQL of the project's job, computed from the run date (D-71). Ad-hoc batches get it from the intake row. Files carry it in the name. |
| **`Req_Dt_Key`** | The run date: the actual batch creation date (D-29). Part of the grain, part of `Btch_ID`, and the basis of the SLA hold (D-77). |
| **`Btch_ID`** | `{Req_Dt_Key:YYYYMMDD}_{Project_Cd}_{Table_Nm}_{Src_ID}_{Run_Ty}_{Cmplnc_Vrsn}_{Seq}`. `Seq` = 1 + the number of batches already created on that `Req_Dt_Key` for the same (project, table, source, run type), computed under lock. Seq is needed because a re-run for a missed date can create several periods on one day. Unique; never changes. |
| **`Load_ID`** | One physical file (`ComplianceFileLoad`). Separates the original file, replacements and corrections within the same `Btch_ID`. |
| **Resolution** | `NEW_FILE` (the batch has a promoted load), `CARRY_FORWARD` (an approved carry-forward reuses an earlier batch's data, D-70), `MISSING` (closed with no usable data), or `NULL` (open, no usable data yet). |
| **SLA hold** | `Req_Dt_Key + (SLA_Days − 1)`, computed from the run type, never stored (D-38, D-77). The run cannot close before the start of that calendar day in `BUSINESS_TZ`. |
| **Extract grain (a run)** | `(Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key)`. All sources' batches of one run date roll up into one row (D-77). |
| **Eligibility** | Whether a run may be closed, and whether that happens automatically or manually (§11). |
| **Close** | The framework's decision that a run's data is final: every batch of the run is resolved and closed, and the batch list is frozen (D-39). The project's job chain generates the extract afterwards (D-76). |
| **Override** | A manual, approved decision on one batch, valid through `Valid_Thru_Dt_Key`: `REUSE`, `LATE_ARRIVAL` or `CORRECTION` (D-74). |
| **Late arrival / correction** | A file for a closed batch: `LATE_ARRIVAL` when the batch has no data, `CORRECTION` when it has. Both need an approved override (D-04, D-74). |
| **Carry-forward (`REUSE`)** | An approved override that lets an open batch without data reuse an earlier batch's data; only for run types with `Carry_Fwd_Ind = 1` (D-70). |
| **Job setting** | A value passed to a scheduled job (`--set`), or read from the environment / `.env` (D-67). |

---

## 5. Data Model

The DDL is `src/framework/sql/schema.sql` (Appendix A).
- **Schema:** configurable metadata schema (default `cms_compliance`, D-65). The DDL is unqualified and applied with `search_path` set to that schema. PascalCase names are unquoted, so Postgres folds them to lowercase.
- **Database:** one PostgreSQL database holds the framework tables and the staging/core tables (D-65). No extensions are required.
- **13 tables:** 6 reference/configuration, 5 control, 2 audit. Removed in v4: `ComplianceRequestStatus`, `ComplianceRequestStatusTransition`, `CompliancePeriodStrategy`, `ComplianceDbConnection`, `ComplianceFrameworkSetting`, `ComplianceExtractPolicy`, `ComplianceExtractJobParam`. Removed in v5: `ComplianceExtractTrigger` (D-76). Removed in v6: `ComplianceEventType` (the vocabulary is code); added `ComplianceProject`.
- **Review phase:** `schema.sql` is a set of `CREATE` statements only. There are no `ALTER` migrations; a changed model is re-created.
- **DDL standard (v6):**
  - no `CHECK` constraints: values are validated by the framework and by `validate-config`;
  - every constraint and index is named (`pk_`, `fk_`, `uq_`, `ix_`), and foreign keys tie every `Project_Cd`, `Src_ID` and `Run_Ty` to its reference table;
  - only indexes that enforce a rule or serve a query the framework runs on a growing table.
- **Column names** follow `ComplianceRequestControl`: a class-word suffix — `_ID`, `_Cd`, `_Nm`, `_Desc`, `_Ty`, `_Stat`, `_Ind` (0/1), `_Cnt`, `_Dt_Key` (date), `_Dtts` (`TIMESTAMPTZ`, UTC), `_Txt`, `_List`, `_By`.
- **Audit columns:** tables people maintain carry `Created_Dtts` / `Created_By` / `Updated_Dtts` / `Updated_By`; tables only the framework writes carry `Created_Dtts` / `Updated_Dtts`; append-only logs carry `Event_Dtts`.

### 5.1 Reference and configuration

| Table | Key | Purpose / rules |
|---|---|---|
| `ComplianceProject` | `Project_Cd` | One row per project: `Project_Desc`, `Active_Ind`. Every `Project_Cd` in the framework references it; one framework instance serves many projects. |
| `ComplianceSourceSystem` | `Src_ID` | Source master: `Src_Nm`, `Src_Ty`. |
| `ComplianceRunType` | `Run_Ty` | `Run_Category_Cd` (`ROUTINE` scheduled / `ADHOC` requested). `SLA_Days ≥ 1` (hold, D-38). `Carry_Fwd_Ind` (D-70). Letters and digits only (it is the `{RUNTY}` token). |
| `ComplianceDataSetSourceXwalk` | `(Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key)` | Which (project, table, source, run type) combinations apply and when: `Cmplnc_Vrsn` (part of `Btch_ID`), `Active_Ind`, effective window. Windows for the same 4-part key must not overlap — `validate-config` reports `XWALK_OVERLAP` (D-51). Nothing about schedules, periods or time zones (D-71). |
| `ComplianceSourceFileConfig` | `Cfg_ID`; one active row per `(Project_Cd, Table_Nm, Src_ID)` (D-33) | The file contract (below). |
| `ComplianceRuleBinding` | `(Project_Cd, Table_Nm, Src_ID, Rule_Scope_Cd, Gre_Rule_Group, Gre_Rule_Variant)` | Links a scope to GRE rules. `Src_ID = '*'` means all sources, used for `PERIOD_LEVEL`. A source with no FILE_LEVEL binding skips file rules. The GATE/ANNOTATE modes are job settings (D-44, D-63). ⚠ Q-12 (GRE call details). |

**Job-level configuration (not tables, D-66 – D-72)**

| What | Where |
|---|---|
| Database connection | `.env` locally; Secrets Manager (`FRAMEWORK_DB_SECRET_NAME`) in AWS |
| Runtime settings | `--set` job arguments > environment > `.env` > defaults (`settings.py`) |
| Report period | `run --module BATCH_CREATION --period <NAME>` (`period_sql.py` or `--period-file`), `--lookback-days/weeks` |
| Business time zone | `BUSINESS_TZ` |
| Gating, rule modes, automatic close | `EXTRACT_GATING_MODE`, `PERIOD_RULES_MODE`, `FILE_RULES_MODE`, `AUTO_CLOSE_EXTRACTS` |
| Load engine | `LOAD_ENGINE` |
| Quarantine location | `QUARANTINE_URI` (files go under `<reason>/`) |
| Email | `NOTIFY_BACKEND` (`log` / `ses`), `NOTIFY_FROM_EMAIL`, `DEFAULT_NOTIFY_EMAILS` |
| `Req_Stat` values and transitions | `common.TRANSITIONS` (D-69) |
| Event vocabulary | `audit.BATCH_EVENTS`, `audit.AUDIT_EVENTS` (category, severity, emailed) |

**`ComplianceSourceFileConfig` columns**

| Group | Columns |
|---|---|
| Identity | `Project_Cd`, `Table_Nm`, `Src_ID` |
| Filename | `Src_File_Nm_Tmplt` (D-28): project, table and source written literally; the extension is the file type |
| File format | `Delmtr_Cd`, `Src_File_Has_Hdr_Ind`, `Src_File_Has_Trlr_Ind` (⚠ Q-02); LF and CRLF line endings are both read |
| Handling | `Allow_Zero_Rcd_Ind` (D-60) |
| S3 paths | `S3_Src_File_Path` (inbound), `Src_File_Archive_Path` |
| Targets | `Stg_Schema_Nm`, `Stg_Table_Nm`; core table `Core_Schema_Nm`.`Table_Nm` (D-25) — same database |
| Email | `Sucs_Email_Notfn_Id`, `Failr_Email_Notfn_Id` (comma-separated recipients), `Email_Subjct_Txt` |

**`config.validate_all` rejects:**
- Templates missing a required placeholder, using a placeholder twice, or using an unknown one (§9.1); a template without a file extension.
- Two active configs that could match the same filename (§9.3).
- A file config with no active crosswalk row, or an active crosswalk row with no file config; overlapping crosswalk windows.
- A run type whose code is not letters/digits, whose category is not `ROUTINE`/`ADHOC`, or whose `SLA_Days < 1`.
- Staging / core tables missing or lacking framework columns.
- Inbound / archive paths that are not `s3://` URIs.
- It warns about crosswalk rows whose run type or project is inactive.
- Job-level values are checked when they are used: an unknown period name or a missing lookback raises `ConfigError` and the command exits 2.

### 5.2 Control

**`ComplianceRequestControl` (CRC)**, one row per batch:

| Column | Notes |
|---|---|
| `Req_ID` | Identity PK |
| `Project_Cd`, `Table_Nm`, `Src_ID`, `Run_Ty`, `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key`, `Req_Dt_Key` | **Unique grain (D-30, D-77)** — one batch per source per report period **per run date** |
| `Req_Dt_Key` | Actual creation date (D-29); also the basis of the SLA hold (D-77) |
| `Btch_ID` | Unique, immutable (D-03); carries the compliance version |
| `Extract_ID` | The run this batch belongs to |
| `Req_Stat` | Fixed list (D-69, §6.1) |
| `Resolution_Ty` | `NEW_FILE` / `CARRY_FORWARD` / `MISSING` / NULL |
| `Reuse_Btch_ID` | `CARRY_FORWARD` only: the batch whose current data is reused (D-70) |
| `Batch_Close_Ind` | Close state (D-39) |
| `Created_Dtts`, `Updated_Dtts` | Audit timestamps |

Whether a batch was scheduled or requested follows from its run type's `Run_Category_Cd` (no `Created_By`). There is no `Earliest_Close_Dt` (computed, D-77), no `Intake_ID` (D-79), no `Current_Load_ID` (D-75) and no `Closed_By_Trigger_ID` (D-76). The framework keeps `Reuse_Btch_ID` set exactly when `Resolution_Ty = 'CARRY_FORWARD'`, and resolves every batch it closes.

**`ComplianceFileLoad`**, one row per physical S3 object, including quarantined files:

| Group | Columns |
|---|---|
| S3 object | `S3_Bucket`, `S3_Key`, `S3_Version_Id`, `S3_ETag`, `File_Size_Byte`, `File_Sha256` |
| Links | `Cfg_ID`, `Req_ID`, `Btch_ID` (report period and run type come from the batch) |
| Status | `Load_Stat`, `Rules_Stat`, `Quarantine_Rsn_Cd`, `Error_Txt` |
| Counts | `Stg_Rcd_Cnt` (checked against the rows appended to core) |
| Time | `Created_Dtts` (received), `Updated_Dtts` (last progress; stale-load health check) |

Unique on `(bucket, key, COALESCE(version, etag))`. A **partial unique index** (`uq_compliancefileload_promoted`) allows at most one `PROMOTED` row per `Btch_ID`: that row *is* the batch's current data (D-75).

**`ComplianceBatchOverride`** — one shape for all three manual decisions (D-74):

| Column(s) | Notes |
|---|---|
| `Override_Ty` | `REUSE` / `LATE_ARRIVAL` / `CORRECTION` |
| `Req_ID`, `Btch_ID` | Always required: every override is about one batch; the rest of the batch is read through `Req_ID` |
| `Reuse_Btch_ID` | `REUSE`: optional on request; set to the reused batch when it is applied |
| `Rsn_Txt` | Why it was requested (or rejected) |
| `Apprvl_Stat` | `PENDING_REVIEW` / `APPROVED` / `REJECTED` |
| `Created_By`, `Reviewed_By`, `Reviewed_Dtts` | Requester; who approved or rejected it, and when |
| `Valid_Thru_Dt_Key` | Required when `APPROVED`: the last date the override may be used. Moving it into the past stops the override — there is no revoke (D-74) |

`uq_compliancebatchoverride_active (Req_ID, Override_Ty) WHERE Apprvl_Stat IN ('PENDING_REVIEW','APPROVED')` enforces D-02. There are no candidate/reviewed loads, no promotion status and no revocation.

**`ComplianceExtractControl`**, one row per extract grain:

| Column(s) | Notes |
|---|---|
| Grain | Project, table, run type, report period, **`Req_Dt_Key`** (D-77) |
| `Required_Src_Cnt` | Snapshot, see below |
| `Received_Src_Cnt` | Sources with data (own file or carried forward); which ones, and which are carried, is read from the batches |
| `Extract_Rules_Stat`, `Failed_Rule_List` | `PENDING` / `PASSED` / `PASSED_WITH_WARNINGS` / `FAILED` / `ERROR`; the failed period rules |
| `Eligibility_Cd`, `Eligibility_Rsn_Txt` | `NOT_ELIGIBLE` / `MANUAL_ONLY` / `AUTO`; reason text, including the warnings |
| `Extract_Close_Ind`, `Extract_Closed_Dtts`, `Extract_Closed_By` | Close state (D-39); acknowledged warnings are in the `EXTRACT_CLOSED_WITH_WARNINGS` event |
| `Regenerate_Required_Ind` | Set when the data changed after the close (a late arrival or correction was promoted, D-41) |
| `Combine_Btch_ID_List`, `Combine_Last_Run_Dtts` | The batch list the extract job reads, and when it was last combined |
| `Data_Signature`, `Closed_Data_Signature` | Hash of the (Btch_ID, Load_ID) pairs at the last combine and at the close |

The SLA hold is not stored: it is `Req_Dt_Key + (SLA_Days − 1)` of the run type (D-77).

`Required_Src_Cnt` is a snapshot of the active, effective crosswalk rows for (project, table, run type) when the row is created. For ADHOC it counts the batches the intake created, and a source that joins later raises it. ⚠ Q-05.

**`ComplianceRequestInTake`:**

| Column(s) | Notes |
|---|---|
| `Intake_ID` | Identity PK |
| `Project_Cd`, `Table_Nm`, `Run_Ty` | Required; the run type must be in the **ADHOC** category (D-79). `Run_Ty` is the framework run type (SLA, carry-forward, `Btch_ID`) |
| `Src_ID` | Null = all configured sources |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | The report period the batches are for |
| `Req_Start_Dt_Key`, `Req_End_Dt_Key` | The **request window**: batches are created for every run date from the start through the end date. Equal dates = a one-off (D-79) |
| `Last_Run_Dt_Key` | The last run date the daily sweep handled this request |
| `Created_By`, `Created_Dtts` | Who asked and when |

Each `BATCH_CREATION` run handles a request once per run date while `Req_Start_Dt_Key ≤ today ≤ Req_End_Dt_Key`; a second run the same day changes nothing (D-35). There is no status column: a request is due while its window is open. Its outcomes are in the audit tables under its `Intake_ID` — `BATCH_CREATED` for each batch, `INTAKE_FAILED` with the reason when it cannot be served (repeated on each run date of the window until the configuration is fixed).

### 5.3 Audit (append-only; written only through `audit.EventLogger`)
- **`ComplianceRequestFileDetail`:** per-batch events (`Req_ID` NOT NULL, plus `Btch_ID`, `Load_ID`, `Ovrd_ID`, `Intake_ID`, `Event_Ty`, `Actor`, `Event_Txt`, `Event_Dtts`). A manual decision carries its `Ovrd_ID`.
- **`CMS_ComplianceExceptionsAudit`:** all other audit and exception events, including files that never matched a batch. It carries `Sevrty`, optional context ids (project, table, source, run type, `Req_ID`, `Load_ID`, `Ovrd_ID`, `Extract_ID`, `Intake_ID`, `Btch_ID`, `File_Ref`), `Actor`, `Event_Txt` and `Notified_Ind` (0 = waiting to be emailed).
- **No PHI** in any free-text field or notification.

**Event catalog** (FD = FileDetail, EA = ExceptionsAudit; severity in brackets; ✉ = emailed):

| Area | FD events | EA events |
|---|---|---|
| Batches | `BATCH_CREATED` [I], `BATCH_CLOSED` [I] | `INTAKE_FAILED` [E ✉], `BATCH_CREATE_SKIPPED_EXTRACT_CLOSED` [W ✉] |
| File intake | `FILE_RECEIVED` [I], `FILE_REPLACED_BEFORE_CLOSE` [I] | `FILE_REJECTED_UNPARSEABLE`, `FILE_REJECTED_AMBIGUOUS_TEMPLATE`, `FILE_REJECTED_INVALID_TOKEN`, `FILE_REJECTED_RUNTY_NOT_CONFIGURED`, `FILE_REJECTED_NO_BATCH`, `FILE_REJECTED_BATCH_CLOSED`, `FILE_PARSE_ERROR`, `FILE_COLUMN_COUNT_MISMATCH`, `FILE_TRAILER_COUNT_MISMATCH`, `FILE_ZERO_RECORDS_REJECTED`, `FILE_TYPE_NOT_SUPPORTED` [E ✉]; `FILE_REJECTED_DUPLICATE`, `FILE_SAME_CONTENT_OTHER_BATCH` [W ✉]; `FILE_EVENT_REPLAY_IGNORED` [I]; `FILE_MOVE_FAILED` [W] |
| Validation / load | `FILE_PROMOTED` [I], `FILE_RULES_FAILED` [E] | `RULES_VALIDATION_FAILED`, `RULES_ENGINE_TECHNICAL_FAILURE`, `CORE_LOAD_ROWCOUNT_MISMATCH` [E ✉] |
| Overrides | `LATE_ARRIVAL_PROMOTED` [I], `CORRECTION_PROMOTED` [I], `CARRY_FORWARD_APPLIED` [I], `CARRY_FORWARD_REMOVED` [I] | `OVERRIDE_APPROVED` [W ✉], `OVERRIDE_EXPIRED` [W ✉], `OVERRIDE_INVALID_DETECTED` [E ✉] |
| Extract | — | `PERIOD_RULES_FAILED` [E ✉], `EXTRACT_ELIGIBILITY_CHANGED` [I], `EXTRACT_CLOSED` [I ✉], `EXTRACT_CLOSED_WITH_WARNINGS` [W ✉], `EXTRACT_CLOSE_BLOCKED` [W], `EXTRACT_CLOSE_DEFERRED_LOCKED` [I], `EXTRACT_REGENERATE_REQUIRED` [W ✉], `SOURCE_MISSING_AT_CLOSE` [W ✉] |
| Config | — | `CONFIG_VALIDATION_FAILED` [E ✉] |

The vocabulary is `audit.BATCH_EVENTS` / `audit.AUDIT_EVENTS`; writing an event outside it is an error. Emailed EXCEPTION events go to the file config's failure list, other emailed events to its success list, and events not tied to one file config to `DEFAULT_NOTIFY_EMAILS`.

### 5.4 Target tables (framework columns)

| Table | Columns | Index |
|---|---|---|
| Staging (never truncated) | `Btch_ID`, `Load_ID`, `Src_File_Nm`, `Stg_Load_Dtts` | `(Btch_ID)` |
| Core | `Btch_ID`, `Load_ID`, `Current_Ind`, `Load_Dtts`, `End_Dtts` | `(Btch_ID) WHERE Current_Ind = 1`, `(Load_ID)`; partition by month (D-57) |

- Positional mapping (D-59) maps file column *n* to the *n*-th **business** column of the staging table in ordinal order. Business columns are all columns except the four framework columns.
- Many sources can share one staging/core table, because every operation is scoped by `Btch_ID`.

---

## 6. State Machines

### 6.1 `Req_Stat` (D-69)
The values and the legal moves are `common.TRANSITIONS` (any other move raises `InvalidStatusTransition`).

| Value | Open / closed | Meaning |
|---|---|---|
| `PENDING` | open | No usable data yet |
| `PROMOTED` | open | Current data promoted |
| `CARRIED_FORWARD` | open | Approved carry-forward reuses an earlier batch's data (D-70) |
| `EXCEPTION_PENDING` | open | The latest file failed GATE (prior or carried data may still be current, D-46) |
| `COMPLETED` | closed | Closed with data (own or carried) (D-39) |
| `COMPLETED_WITH_EXCEPTION` | closed | Closed while in `EXCEPTION_PENDING` |
| `DATA_NOT_PROVIDED` | closed | Closed with no data (`MISSING`) |

| From | To | Trigger |
|---|---|---|
| — | `PENDING` | Batch created |
| `PENDING` / `EXCEPTION_PENDING` / `CARRIED_FORWARD` | `PROMOTED` | Core swap committed (a file replaces a carry-forward) |
| `PENDING` / `PROMOTED` / `CARRIED_FORWARD` | `EXCEPTION_PENDING` | File failed GATE |
| `PENDING` / `EXCEPTION_PENDING` | `CARRIED_FORWARD` | `REUSE` override applied (D-70) |
| `CARRIED_FORWARD` | `PENDING` | `REUSE` override ran out (D-74) |
| `PROMOTED` / `CARRIED_FORWARD` | `COMPLETED` | Run closed |
| `EXCEPTION_PENDING` | `COMPLETED_WITH_EXCEPTION` | Run closed (prior data or none) |
| `PENDING` | `DATA_NOT_PROVIDED` | Run closed |
| `COMPLETED` / `COMPLETED_WITH_EXCEPTION` / `DATA_NOT_PROVIDED` | `COMPLETED` | Late arrival or correction promoted (stays closed, D-04) |

### 6.2 Override `Apprvl_Stat` (D-74)
```
[*] -> PENDING_REVIEW (SQL insert) -> APPROVED  (SQL, sets Valid_Thru_Dt_Key)
                                   -> REJECTED  (SQL)
APPROVED  --(today > Valid_Thru_Dt_Key)-->  no longer usable
```
- There is no `REVOKED`: an approval is stopped by moving `Valid_Thru_Dt_Key` into the past (template 6).
- An applied `REUSE` whose date has passed is removed by the next `process-decisions` run (`OVERRIDE_EXPIRED`, `CARRY_FORWARD_REMOVED`) and its batch returns to `PENDING`.
- A rejected row frees the (`Req_ID`, `Override_Ty`) slot, so a new row of that type can be requested.

### 6.3 Extract
- **Completeness** is not stored (v6): the run is complete when `Received_Src_Cnt ≥ Required_Src_Cnt` (own file or carried forward).
- **Closure:** `Extract_Close_Ind = 1` from the close (automatic or manual). It never goes back to 0; a later promotion into one of its batches sets `Regenerate_Required_Ind` instead (D-41).

---

## 7. Process Flows

Each flow is an idempotent service. **State changes and their audit events commit in the same transaction.**

**P1. Config change.** Apply rows (Project → SourceSystem → RunType → Xwalk → SourceFileConfig → RuleBinding), then run `validate-config`. Job-level values (period, gating and rule modes) are set on the project's scheduled jobs (D-71, D-72). Any failure → `CONFIG_VALIDATION_FAILED` and the change is not activated. Deactivation is soft (`Active_Ind`, `Effective_End_Dt_Key`). A new compliance version end-dates the old crosswalk row and inserts a new one (D-51).

**P2. Scheduled batch creation (`run --module BATCH_CREATION --project --run-type --period [--table] [--as-of]`).** The external schedule of the project runs this command (D-71):
1. The run type must be active and ROUTINE.
2. **Run date** = `as_of` (default now) in `BUSINESS_TZ`. It is also `Req_Dt_Key` (D-29).
3. Compute the report period from the run date with the named period SQL (`period_sql.py` or `--period-file`; lookback arguments where the statement needs them).
4. Take the active crosswalk rows of the project / run type (optionally one table) that are effective on the run date. `Required_Src_Cnt` per table = the number of those rows.
5. For each row: **ensure the extract row exists first** for (project, table, run type, report period, **run date**) — insert-if-absent with the `Required_Src_Cnt` snapshot; skip if that extract is already closed (`BATCH_CREATE_SKIPPED_EXTRACT_CLOSED`); then `INSERT` the batch unless it exists (D-30, D-77 — a second run on the same date finds it).
6. On insert: `Seq` / `Btch_ID` under the table/source/run-type lock; `PENDING`; log `BATCH_CREATED`.

Running the same period on ten consecutive days therefore produces ten batches and ten extracts, each with its own SLA hold (D-77).

**P3. Missed runs.** There is no catch-up job. Re-run P2 with `--as-of <missed date>`: the period follows that date and `Req_Dt_Key` is that date. (v3's cron expansion, go-live date and lookback horizon are removed.)

**P4. Ad-hoc intake (every `run --module BATCH_CREATION`, D-79).** Take one due row at a time with `FOR UPDATE SKIP LOCKED` — today (in `BUSINESS_TZ`, or `--as-of`) is inside `[Req_Start_Dt_Key, Req_End_Dt_Key]` and `Last_Run_Dt_Key` is not today:
1. Set `Last_Run_Dt_Key = today` (a second run the same day skips the row, D-35).
2. The run type must be active and in the **ADHOC** category; otherwise `INTAKE_FAILED` with the reason.
3. Fan out to the active crosswalk rows effective for the report period (or the one named `Src_ID`); zero sources → `INTAKE_FAILED`.
4. Create the batches for **today's run date** and the intake's report period, exactly as P2 does (extract row first); each `BATCH_CREATED` carries the `Intake_ID`. Batches that already exist for that run date are left alone.
A request outside its window is never picked up; a failing request is reported again on each run date of its window until its configuration is fixed.

A correction is **not** an intake row: it is a `CORRECTION` override on the batch (D-74).

**P5. File ingest (`ingest-file`).** Pre-checks C0–C10 are listed in §9.4. Then:
1. **Lock** the batch (session advisory lock on `Btch_ID`).
2. **Duplicate check (C11)** and **read / structural check (C12–C14)**.
3. **Stage** (D-05): delete staging rows by `Btch_ID`, load with `Load_ID`, record counts.
4. **Zero-record check (D-60):** if the data row count is 0 and `Allow_Zero_Rcd_Ind = 0` → `FILE_ZERO_RECORDS_REJECTED`, `Load_Stat = RULES_FAILED`, treated like a GATE failure in §8.
5. **File-level rules** (when the source has FILE_LEVEL bindings): GRE with `{btch_id, load_id}`.
   - GATE failure (mode `FILE_RULES_MODE`, D-44) → whole-file failure (D-19).
   - ANNOTATE-only failures → passed with warnings.
   - A technical failure → `FAILED_TECHNICAL`, raise and retry; CRC unchanged.
6. **Resolve** per §8.
7. **Archive** the S3 object after commit. A failure here is retried when the event replays (C0).
8. **Release** locks.
9. If anything was promoted → refresh the extract (early-completion check, D-21). For a promotion into a **closed** batch, the refresh also sets `Regenerate_Required_Ind` (D-41).
10. If the batch was `CARRIED_FORWARD`, the promoted file replaces the carried data: `Reuse_Btch_ID` is cleared and `CARRY_FORWARD_REMOVED` is logged. The `REUSE` override row is left as it is; it no longer applies because the batch has data (D-70).

**Which batch a file belongs to (D-78).** Filenames carry the report period but not the run date, so the pipeline picks the **open** batch of the grain with the latest `Req_Dt_Key ≤ today`. If every batch of the grain is closed, it takes the most recent one and requires an approved, still-valid override of the type that matches its state (`LATE_ARRIVAL` with no data, `CORRECTION` with data); without one the file is quarantined as `FILE_REJECTED_BATCH_CLOSED`. That quarantine is **retryable**: delivering the same object again after the override exists reprocesses the same `Load_ID`.

**P5b. Path ingest (`ingest-path [--bucket --prefix]`).** A thin sweep over P5: `ingest.IngestPipeline.process_path` lists every object waiting at one inbound location (`--bucket`/`--prefix`), or — when both are omitted — at the distinct inbound location of every active file config (config-driven, D-27; several source configs commonly share one folder, matched purely by filename template). Every object found is handed to the same `process_file` used by `ingest-file`, one at a time, so each one is still matched to exactly **one** file config and resolved against exactly **one** batch (D-26, D-33) under that batch's own lock — several files for different sources, different configs and different open batches are simply all picked up and processed in the one call. A location that cannot be listed, or an object that fails technically, is recorded and does not stop the rest of the sweep. `ingest-path` exists so an orchestrator (or a poll) can say "process whatever is waiting" instead of enumerating objects and calling `ingest-file` once per file; it changes nothing about how a single object is resolved or promoted.

**P6. Promotion.** Core swap per §10.2.

**P7. Decisions (`process-decisions`, D-74).** The job only handles `REUSE`; `LATE_ARRIVAL` and `CORRECTION` are read by the ingest pipeline (§8.2) and need no job.

| Detected | Action |
|---|---|
| `REUSE` `APPROVED`, `Valid_Thru_Dt_Key ≥ today`, batch open and not yet carried | Validate: the run type has `Carry_Fwd_Ind = 1`, the batch is open with no promoted load, and a source batch exists (the requested `Reuse_Btch_ID`, else the latest earlier **closed** batch of the same project/table/source/run type that has data; a carried batch resolves to its own source). Invalid → `OVERRIDE_INVALID_DETECTED`, batch unchanged. Valid → CRC `Resolution_Ty = CARRY_FORWARD`, `Reuse_Btch_ID`, `Req_Stat = CARRIED_FORWARD`; log `OVERRIDE_APPROVED` + `CARRY_FORWARD_APPLIED`; refresh the extract. |
| Carried batch whose override ran out, was rejected or is gone | CRC back to `PENDING`, `Resolution_Ty` and `Reuse_Btch_ID` cleared; log `OVERRIDE_EXPIRED` + `CARRY_FORWARD_REMOVED`; refresh the extract. Closed batches are never touched. |

The job is idempotent: an already-applied override is skipped, and an already-expired one has nothing left to remove.

**P9. Refresh (`refresh-extract`).** Under the extract lock:
1. **Recount.** Received = batches with `NEW_FILE` or `CARRY_FORWARD`; update `Received_Src_Cnt`.
2. **Decide whether to combine.** Combine runs if the trigger is early completion and the period is complete, or if it is an SLA evaluation, a manual close, a late/correction promotion, an override decision or a manual refresh. A combine is skipped when the data signature is unchanged and the rule result is still valid (`MANUAL_REFRESH` always re-runs the rules).
3. **Combine** (§10.4), then run the **period-level rules** with mode `PERIOD_RULES_MODE` (D-63). Set `Extract_Rules_Stat`, and log the contributing `Btch_ID`s for each failed rule (`PERIOD_RULES_FAILED`).
4. **Update** `Combine_Btch_ID_List`, `Combine_Last_Run_Dtts` and `Data_Signature`.
5. **Compute** eligibility (§11.2).
   - If it changed → `EXTRACT_ELIGIBILITY_CHANGED`.
   - If the extract is closed and the data signature differs from `Closed_Data_Signature` → `Regenerate_Required_Ind = 1`, `EXTRACT_REGENERATE_REQUIRED` (D-41).

**P10. Evaluate and close (`evaluate-extracts [--project] [--table] [--run-type] [--as-of]`).** For every open extract in the job's scope whose SLA hold has passed — `Req_Dt_Key + (SLA_Days − 1) ≤ today`, joined from the run type (D-77) — refresh (P9) and, when `AUTO_CLOSE_EXTRACTS` is on and `Eligibility_Cd = AUTO`, close it (§11.3). Runs that are not eligible are counted, locked ones are deferred, and every extract with `Regenerate_Required_Ind = 1` in scope is listed in the summary so the project's job chain can pick it up.

**P11. Manual close (`close-extract --extract-id --closed-by [--ack-warnings]`).** Refresh first, then apply the §11.2 rules:
- Not eligible → `EXTRACT_CLOSE_BLOCKED`, exit 2, nothing changes.
- `MANUAL_ONLY` with warnings → requires `--ack-warnings`; the warnings are logged in `EXTRACT_CLOSED_WITH_WARNINGS`.
- Already closed → blocked.

**P12. Extract generation (outside the framework, D-76).** After the close, the project's job chain reads the closed extract row — `Combine_Btch_ID_List`, the report period, `Req_Dt_Key` — generates the submission and, when `Regenerate_Required_Ind = 1`, generates it again.

**P13. Notifications.**
- Every notification is an email (D-54). Which events are emailed is fixed in `audit.AUDIT_EVENTS`; the others are written with `Notified_Ind = 1`.
- Recipients come from the file config (failure list for EXCEPTION events, success list otherwise) or `DEFAULT_NOTIFY_EMAILS`.
- Set `Notified_Ind` after sending. A failed notification never rolls back pipeline state.

---

## 8. File-Resolution Decision Tables

These apply after C0–C14 pass, with the batch lock held, to the batch chosen by D-78. A batch with an applied carry-forward counts as "has data" (so O-2 / O-4 apply). **PASS** = file-level rules passed (with or without warnings) and the zero-record rule is satisfied. **FAIL** = GATE failure or a disallowed zero-record file.

### 8.1 Batch OPEN (`Batch_Close_Ind = 0`)

| # | Batch has data? | File | CRC | Core | Load_Stat | Events |
|---|---|---|---|---|---|---|
| O-1 | no | PASS | `NEW_FILE`, `PROMOTED` | swap | `PROMOTED` | `FILE_RECEIVED`, `FILE_PROMOTED` |
| O-2 | yes | PASS | `NEW_FILE`, `PROMOTED` (latest arrival wins even if `{TS}` is older, D-37; carried data is replaced, D-70) | swap (prior rows disabled) | this `PROMOTED`, prior `SUPERSEDED` | + `FILE_REPLACED_BEFORE_CLOSE` (+ `CARRY_FORWARD_REMOVED`) |
| O-3 | no | FAIL | `Resolution` NULL, `EXCEPTION_PENDING` | none | `RULES_FAILED` | `FILE_RULES_FAILED`, `RULES_VALIDATION_FAILED` |
| O-4 | yes | FAIL | keeps the prior data (or the carry-forward), `EXCEPTION_PENDING` (D-46) | none | `RULES_FAILED` | same as O-3 |
| O-5 | any | technical error | unchanged | rolled back | `FAILED_TECHNICAL` | `RULES_ENGINE_TECHNICAL_FAILURE`; retry |

### 8.2 Batch CLOSED (`Batch_Close_Ind = 1`, D-74)

The pipeline reads the batch's approved, still-valid override (`Valid_Thru_Dt_Key ≥ today`) of the type its state requires: `LATE_ARRIVAL` when it has no data, `CORRECTION` when it has.

| # | Override | File | Result | CRC | Core | Load_Stat | Events |
|---|---|---|---|---|---|---|---|
| C-1 | valid `LATE_ARRIVAL`, batch has no data | PASS | promoted late | `NEW_FILE`, `COMPLETED`, stays closed (D-04) | swap | `PROMOTED` | `FILE_RECEIVED`, `LATE_ARRIVAL_PROMOTED`, then `EXTRACT_REGENERATE_REQUIRED` |
| C-2 | valid `CORRECTION`, batch has data | PASS | correction promoted | `NEW_FILE`, `COMPLETED` | swap (prior rows disabled) | this `PROMOTED`, prior `SUPERSEDED` | + `CORRECTION_PROMOTED` |
| C-3 | none, wrong type, or run out | any | quarantined | unchanged | none | `QUARANTINED` (`FILE_REJECTED_BATCH_CLOSED`) | `FILE_REJECTED_BATCH_CLOSED`; **retryable** — re-deliver once the override exists (D-78) |
| C-4 | valid | FAIL | rejected | unchanged | none | `RULES_FAILED` | `FILE_RULES_FAILED`, `RULES_VALIDATION_FAILED` (D-45) |

Staging a file for a closed batch first deletes the staged rows of that `Btch_ID` (D-05). Core is untouched until the promotion, which happens in the same run — there is no separate approval job and no promotion status.

---

## 9. Filename Templates & Matching (D-28 – D-37)

### 9.1 Template grammar

A template is literal text — including the project, table and source as they appear in the file name — plus exactly one of each placeholder:

| Placeholder | Matches | Validation |
|---|---|---|
| `{RUNTY}` | `[A-Za-z0-9]+` (⚠ Q-03 character set) | must equal a `Run_Ty` exactly (D-31) **and** have an active, effective crosswalk row for (project, table, source, run type) (D-33) |
| `{RPTSTART}`, `{RPTEND}` | `\d{8}` | valid `YYYYMMDD` dates (D-32), end ≥ start |
| `{TS}` | `\d{14}` | valid `YYYYMMDDHHMMSS` (D-36); used only for uniqueness (D-37) |

- The extension is part of the literal text, e.g. `…_{TS}.csv`, and is the file type (v6: no `Src_File_Ty` column).
- The template is anchored to the whole object name (the basename after the configured inbound prefix).
- Literal text is regex-escaped.
- **Adjacent placeholders must be separated by at least one literal character.** This keeps parsing unambiguous and is enforced by the validator.
- Case sensitivity: ⚠ Q-03.

**Generic example** (illustrative only): template `PRJA_TBLX_SRC1_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt` matches `PRJA_TBLX_SRC1_MONTHLY_20260101_20260131_20260201093000.txt`.

### 9.2 Matching algorithm
1. Compile every active config's template into a regex (cached; rebuilt on config change).
2. Match the object name against all of them. It must match **exactly one** config.
3. Validate the tokens (§9.1) and resolve `Run_Ty` and its crosswalk row.
4. Look up the batch by the full grain: config's (project, table, source) + `Run_Ty` + (report start, report end).

**Effective-date check for the crosswalk row** (D-51): the row must be effective for the file's report period (⚠ Q-04: which date is used).

### 9.3 Non-overlap (config time)
Two active configs **conflict** if their templates could match the same name. The validator checks this in two ways:
- **Test-based:** for each config, generate sample names with synthetic token values and assert that no other config matches them.

The same check runs again at runtime (C3).

### 9.4 Pre-checks in the ingest pipeline

Every failure except C0 quarantines the file, sets `Load_Stat = QUARANTINED` with a reason, writes an EA event, notifies, and stops. None of them writes to CRC or overrides.

| # | Check | Failure event |
|---|---|---|
| C0 | Object already in `ComplianceFileLoad`: terminal status → ignore (replay); non-terminal with no lock held → restart the same `Load_ID` | `FILE_EVENT_REPLAY_IGNORED` |
| C1 | Zero-byte object when a header is expected | `FILE_PARSE_ERROR` |
| C2 | Matches no template | `FILE_REJECTED_UNPARSEABLE` |
| C3 | Matches more than one template | `FILE_REJECTED_AMBIGUOUS_TEMPLATE` (+ config alert) |
| C4 | Invalid date, timestamp or run-type token (§9.1) | `FILE_REJECTED_INVALID_TOKEN` |
| C5 | Run type not configured or not effective for this source | `FILE_REJECTED_RUNTY_NOT_CONFIGURED` |
| C6 | No batch for the grain (D-15) | `FILE_REJECTED_NO_BATCH` |
| C7–C10 | Reserved | — |
| C11 | SHA-256 equals the batch's current or pending candidate load | `FILE_REJECTED_DUPLICATE` |
| C11 | SHA-256 equals a load of a **different** batch → **continue** (D-52) | `FILE_SAME_CONTENT_OTHER_BATCH` (warning) |
| C12 | Read failure (delimiter, encoding, structure) | `FILE_PARSE_ERROR` |
| C13 | Data column count ≠ staging business-column count (D-59) | `FILE_COLUMN_COUNT_MISMATCH` |
| C14 | Trailer count mismatch (when `Src_File_Has_Trlr_Ind = 1`, ⚠ Q-02) | `FILE_TRAILER_COUNT_MISMATCH` |

- **Zero-byte file with no header expected:** it has zero records and is handled by the D-60 rule in P5 step 4.
- **Retries:** C12–C14 are retried only for transient I/O errors, then quarantined.

---

## 10. Staging, Promotion & Combine Mechanics

### 10.1 Staging (D-05, D-59, D-62)
- **Engine per job (`LOAD_ENGINE`, D-62).**
  - pandas: `COPY` in one transaction, including the preceding `DELETE … WHERE Btch_ID`.
  - Spark: delete, then JDBC append with bounded partitions and `batchsize`.
- **Safe re-runs:** a re-run repeats the delete and reload. Promotion always filters by `Load_ID` and checks row counts, so a partial Spark load can never be promoted.
- **Column mapping:** positional.
  - With a header row, the header is skipped and names are ignored.
  - With a trailer, the trailer is removed before counting.
  - All values are read as text and cast to the staging column types; a cast failure → `FILE_PARSE_ERROR`.

### 10.2 Promotion (D-01): one transaction (D-68)
```sql
-- caller holds the session advisory lock on Btch_ID
BEGIN;
UPDATE <core_schema>.<Table_Nm> SET Current_Ind = 0, End_Dtts = now()
 WHERE Btch_ID = :btch_id AND Current_Ind = 1;                              -- disabled_cnt
INSERT INTO <core_schema>.<Table_Nm> (<cols>, Btch_ID, Load_ID, Current_Ind, Load_Dtts)
SELECT <cols>, Btch_ID, Load_ID, 1, now()
  FROM <stg_schema>.<Stg_Table_Nm> WHERE Btch_ID = :btch_id AND Load_ID = :load_id;  -- appended_cnt
-- appended_cnt <> FileLoad.Stg_Rcd_Cnt  ->  ROLLBACK, CORE_LOAD_ROWCOUNT_MISMATCH
UPDATE ComplianceRequestControl ...; UPDATE ComplianceFileLoad ...; INSERT audit ...;
COMMIT;
```
- **`<cols>`** = staging business columns ∩ core columns, minus core identity/generated/serial columns (from `information_schema`) (D-61).
- **Identifiers** are quoted with `psycopg.sql.Identifier`. No `SELECT *`.
- **Spark is never used for this step.** Spark JDBC can't make the disable and the append atomic.
- **Out-of-order files are safe,** because each swap touches only its own `Btch_ID`.
- **One database (D-65, D-68):** the core statements, the CRC / FileLoad updates and the audit rows are one transaction; a failure anywhere rolls everything back and the replay (C0) starts the load again.

### 10.3 Lineage
`Btch_ID` (which period, source and run date) + `Load_ID` (which physical file) + the batch's single `PROMOTED` load row (which load is current, D-75) + `ComplianceFileLoad` (S3 version, SHA).

### 10.4 Combine
```sql
SELECT c.*
  FROM ComplianceRequestControl r
  JOIN <core_schema>.<Table_Nm> c ON c.Btch_ID = r.Btch_ID AND c.Current_Ind = 1
 WHERE r.Project_Cd = :p AND r.Table_Nm = :t AND r.Run_Ty = :rt
   AND r.Rpt_Start_Dt_Key = :s AND r.Rpt_End_Dt_Key = :e AND r.Req_Dt_Key = :d
   AND r.Resolution_Ty = 'NEW_FILE'
UNION ALL                                   -- carried sources read the reused batch (D-70)
SELECT c.*
  FROM ComplianceRequestControl r
  JOIN ComplianceRequestControl src ON src.Btch_ID = r.Reuse_Btch_ID
  JOIN <core_schema>.<Table_Nm> c ON c.Btch_ID = src.Btch_ID AND c.Current_Ind = 1
 WHERE <same grain> AND r.Resolution_Ty = 'CARRY_FORWARD';
```
- `Current_Ind = 1` already selects the promoted load of each batch (D-75).
- Period-level GRE rules receive `{btch_id_list}` / `{load_id_list}` and read core directly (same DB, D-43).
- The same `Btch_ID` list (reused batch ids included) is stored in `Combine_Btch_ID_List`, which is what the project's extract job reads after the close (D-76).

---

## 11. Run Close (D-38 – D-41, D-49, D-76)

### 11.1 Hold
- **Per run:** `earliest_close = Req_Dt_Key + (SLA_Days − 1)`, from the extract's run date and its run type (D-77). It is computed, never stored.
- **Rule:** before that date begins (in `BUSINESS_TZ`), **no close of any kind is allowed.** The hold is absolute.

### 11.2 Eligibility (computed on every refresh)

`complete = Received ≥ Required` (Received includes carried-forward sources, D-70). The mode is `EXTRACT_GATING_MODE` (D-72). `rules_ok = Extract_Rules_Stat IN (PASSED, PASSED_WITH_WARNINGS)`.

| Mode | `AUTO` when | `MANUAL_ONLY` when | `NOT_ELIGIBLE` when |
|---|---|---|---|
| any | Hold passed **and** `complete` **and** `rules_ok` (D-40) | — | Hold not passed; no sources required |
| `STRICT_ALL_PASS` | (above) | — | Otherwise: any missing source, or failed period rules |
| `BEST_EFFORT` | (above) | Hold passed, and anything else: partial, failed rules or zero data. The warnings are listed and must be acknowledged (D-40, D-49) | Hold not passed |

- `Extract_Rules_Stat = ERROR` (technical failure) → `NOT_ELIGIBLE` until a refresh succeeds.
- `Extract_Rules_Stat = PENDING` → `NOT_ELIGIBLE` (combine has not run for the current data).
- A change of `Eligibility_Cd` is logged as `EXTRACT_ELIGIBILITY_CHANGED`.

### 11.3 Close (the only way a batch closes, D-39)

Under the extract lock, plus a **try-lock on every open batch of the run**. If any batch is busy → `EXTRACT_CLOSE_DEFERRED_LOCKED`; the sweep tries again, a manual close exits non-zero.

1. Refresh (P9) and check eligibility. Blocked → `EXTRACT_CLOSE_BLOCKED` (manual only), nothing changes.
2. In one transaction:
   - **Every open batch of the run:**
     - unresolved → `Resolution_Ty = MISSING`, `DATA_NOT_PROVIDED` (+ `SOURCE_MISSING_AT_CLOSE`);
     - `NEW_FILE` or `CARRY_FORWARD` → `COMPLETED`;
     - in exception → `COMPLETED_WITH_EXCEPTION`.
     - All get `Batch_Close_Ind = 1` and log `BATCH_CLOSED`.
   - Extract: `Extract_Close_Ind = 1`, `Extract_Closed_Dtts`, `Extract_Closed_By` (`SYSTEM` for the automatic close), `Closed_Data_Signature = Data_Signature`, `Regenerate_Required_Ind = 0`.
   - Log `EXTRACT_CLOSED` or `EXTRACT_CLOSED_WITH_WARNINGS`.
3. The project's job chain generates the extract afterwards from `Combine_Btch_ID_List` (D-76). The framework does not call it and does not track it.

### 11.4 After a late arrival or correction (D-41)
1. The file is promoted into the closed batch (§8.2) → refresh → the data signature differs from `Closed_Data_Signature` → `Regenerate_Required_Ind = 1` + `EXTRACT_REGENERATE_REQUIRED`.
2. `evaluate-extracts` lists those runs; the project's job chain regenerates the submission. The flag stays 1 until the run is closed again, which does not happen for an already-closed run, so operations clear it once the regeneration is done (health query `regenerate_required`).

⚠ **Q-16:** regeneration may mean an extract already submitted externally is replaced. That is the project's decision, not the framework's.

### 11.5 Late files after close
§8.2. A new source cannot join a closed run through a file, because batches are created only by the scheduler or an intake (D-15).

---

## 12. Idempotency & Concurrency

### 12.1 Idempotency keys
| Operation | Key / mechanism |
|---|---|
| Batch creation (all paths) | CRC grain; existing batch found under the SEQ lock |
| Extract row | Extract grain; same |
| S3 event | `(bucket, key, version / etag)` (C0) |
| Duplicate content | SHA-256 vs current/pending load of the same batch (C11) |
| Staging | Delete + reload by `Btch_ID` |
| Promotion | One transaction with the CRC update; `Load_Stat = PROMOTED` short-circuit on replay |
| Decisions | State of the batch (already carried / already expired) + `SKIP LOCKED` |
| Intake | `Last_Created_Dt_Key` and the existing batch of that run date + `SKIP LOCKED` |
| Close | `Extract_Close_Ind = 1` short-circuits a second close |

### 12.2 Locks
Session advisory locks (`pg_try_advisory_lock` / `pg_advisory_lock` with a timeout), keyed by hashed strings:

| Key | Used by | Why |
|---|---|---|
| `EXT:<Extract_ID>` | refresh, close | One refresh or close per extract at a time |
| `BTCH:<Btch_ID>` | ingest, promotion, decisions, close | One writer per batch |
| `SEQ:<Project>|<Table>|<Src>|<Run_Ty>|<Req_Dt_Key>` | batch creation | Computing `Seq` |

- **Lock order is always EXT → BTCH.** Ingest takes only BTCH. When ingest needs a refresh, it releases BTCH first.
- Locks are released automatically if a process dies.
- A non-terminal load whose `Updated_Dtts` is older than `HEARTBEAT_STALE_MINUTES` with no lock held means a crash → `health` reports it.
- **Direct DB connections only** (D-55). RDS Proxy / PgBouncer transaction pooling would silently break session locks.
- **Locks live in the framework database** (D-65).

### 12.3 Invariants
**In the database:**
- Unique grains, including the run date (D-77).
- One active override per (batch, type) and one `PROMOTED` load per batch (partial unique indexes, D-02, D-75).
- FKs to the reference tables (project, source system, run type) and between control and audit tables.

**In code / `validate-config` (v6: no CHECK constraints):**
- `Req_Stat` values and transitions (D-69); status and type values are only written by the framework.
- An `APPROVED` override without `Valid_Thru_Dt_Key` is never used (every read requires `Valid_Thru_Dt_Key ≥ today`).
- Non-overlapping crosswalk effective windows (`XWALK_OVERLAP`); run type code, category and `SLA_Days`.

### 12.4 Manual-approval race
**Race:** a reviewer approves an override while files keep arriving for that batch.

**Guard (v5, D-74):** an approval is about the **batch**, not about a particular file, so a newer file cannot invalidate it. What bounds it is `Valid_Thru_Dt_Key`: the approver says until when data may be promoted into that closed batch. The Appendix B templates update `WHERE Apprvl_Stat = 'PENDING_REVIEW'` and report 0 rows if the row was already decided; they always set `Valid_Thru_Dt_Key` when approving.

---

## 13. Edge Cases & Negative Scenarios

✔ = handled by a decision or rule in this doc. ⚠ = depends on an open question.

| # | Scenario | Handling | St |
|---|---|---|---|
| E-01 | Name matches no template | C2 quarantine | ✔ |
| E-02 | Name matches more than one template | Prevented by the validator; C3 at runtime | ✔ |
| E-03 | Template matches but the run type isn't configured or effective for that source | C5 | ✔ |
| E-04 | Impossible date (`20260231`), end < start, or bad `{TS}` | C4 | ✔ |
| E-05 | File for a period with no batch (early, or never scheduled) | C6 quarantine (D-15) | ✔ |
| E-06 | File's period differs from the scheduled period (e.g. off by one day) | No batch for that grain → C6; never force-fit | ✔ |
| E-07 | S3 delivers the event twice | C0 | ✔ |
| E-08 | Resend with identical bytes (new `{TS}`) | C11 duplicate | ✔ |
| E-09 | Same bytes as a different period's file | Allowed + warning (D-52) | ✔ |
| E-10 | Resend with an older `{TS}` after a newer file | Latest arrival wins (D-37) | ✔ |
| E-11 | Same object name reused (overwrite) | Prevented by `{TS}`; if it happens anyway, the new version id or ETag is a new load (C0 key) | ✔ |
| E-12 | Zero-byte file | Header expected → C1 parse error; no header → zero-record rule (D-60) | ✔ |
| E-13 | Header-only / zero data rows | D-60 per config | ✔ |
| E-14 | Column count mismatch | C13 | ✔ |
| E-15 | Columns in the wrong order but the same count | **Not detected** (header names not checked, D-59). A GRE rule on content is the only defence | ✔ (accepted risk) |
| E-16 | Type-cast failure while staging | `FILE_PARSE_ERROR` | ✔ |
| E-17 | Corrupt file, wrong delimiter or encoding | C12 | ✔ (Q-02) |
| E-18 | Trailer count mismatch | C14 | ⚠ Q-02 |
| E-19 | Compressed, encrypted or control file arrives | Not supported (D-58) → fails template match or C12 | ✔ |
| E-20 | GATE failure, open batch, no prior data | O-3 | ✔ |
| E-21 | GATE failure, open batch, prior good data | O-4 (D-46) | ✔ |
| E-22 | ANNOTATE-only failures | Promoted; warnings recorded | ✔ |
| E-23 | GRE or DB technical failure | O-5 retry; CRC unchanged | ✔ |
| E-24 | Core swap fails midway | Single transaction, rollback | ✔ |
| E-25 | Appended ≠ staged row count | Rollback + alert | ✔ |
| E-26 | Crash after staging, before swap | Stale-load health report; replay restarts the same load (C0) | ✔ |
| E-27 | Archive move fails after commit | Replay retries the archive only | ✔ |
| E-28 | Two files for one batch seconds apart | BTCH lock serializes; the second is O-2 or C11 | ✔ |
| E-29 | Scheduled job runs twice, or runs daily inside a longer period | Existing batch found under the SEQ lock (D-30) | ✔ |
| E-30 | Several missed periods re-created on one day | `Seq` keeps `Btch_ID` unique | ✔ |
| E-31 | Batch created late (job re-run with `--as-of`) | Period and `Req_Dt_Key` from the `--as-of` date | ✔ |
| E-32 | Crosswalk deactivated or end-dated with history | Soft delete; history untouched | ✔ |
| E-33 | Compliance version changes mid-stream | Effective-dated rows (D-51); the batch keeps the version it was created with | ✔ (Q-04) |
| E-34 | Source removed after the extract row exists | `Required_Src_Cnt` snapshot unchanged; that batch still exists and must resolve | ✔ |
| E-35 | Sources of one extract would have different periods | Validator blocks it | ✔ |
| E-36 | ADHOC intake with no configured sources | `INTAKE_FAILED` | ✔ |
| E-37 | ADHOC intake for a period that already has a batch | That source fails (D-35) | ✔ |
| E-38 | ADHOC intake adds a new source to a run whose extract row already exists | `Required_Src_Cnt` is raised while the run is open; the run is skipped once it is closed (`BATCH_CREATE_SKIPPED_EXTRACT_CLOSED`) ⚠ Q-05 | ⚠ |
| E-39 | Ad-hoc intake run twice on one day | The batches of that run date already exist; nothing changes (D-79) | ✔ |
| E-40 | Ad-hoc intake whose window has passed | `COMPLETED` without creating anything (D-79) | ✔ |
| E-41 | Ad-hoc intake for a run type that is not ADHOC | `FAILED`, `INTAKE_FAILED` (D-79) | ✔ |
| E-42 | Late file for a `MISSING` closed batch | C-1 under an approved `LATE_ARRIVAL` override; otherwise C-3 | ✔ |
| E-43 | Corrected file for a closed batch with data | C-2 under an approved `CORRECTION` override | ✔ |
| E-44 | File arrives with the wrong override type for the batch's state | C-3 quarantine; the right type must be requested (D-47) | ✔ |
| E-45 | Second file after a late arrival was promoted | The batch now has data, so it needs a `CORRECTION` override; otherwise C-3 | ✔ |
| E-46 | Second correction while the `CORRECTION` override is still valid | C-2 again; the prior load is superseded | ✔ |
| E-47 | File for a closed batch fails GATE | C-4; the override is untouched (D-45) | ✔ |
| E-48 | File quarantined because the run was closed, then an override is approved | Re-deliver the object: the same `Load_ID` is reprocessed (D-78) | ✔ |
| E-49 | Override runs out before the file arrives | C-3 quarantine; move `Valid_Thru_Dt_Key` forward or request a new row | ✔ |
| E-50 | Approval stopped early | `Valid_Thru_Dt_Key` into the past; an applied `REUSE` is removed on the next `process-decisions` (D-74) | ✔ |
| E-51 | Hand-written invalid approval | Ignored without `Valid_Thru_Dt_Key`; `OVERRIDE_INVALID_DETECTED` for an invalid REUSE | ✔ |
| E-52 | Two active overrides of one type on a batch | Partial unique index (D-02) | ✔ |
| E-53 | `REUSE` approved, then the real file arrives before the close | O-2: the file wins and the carried data is dropped | ✔ |
| E-54 | Close attempted during the hold | Blocked (§11.1) | ✔ |
| E-55 | STRICT: a source never arrives | The run cannot close automatically; a person closes it under `BEST_EFFORT` with acknowledged warnings | ✔ |
| E-56 | BEST_EFFORT: zero data | Manual close with a warning (D-49) | ✔ |
| E-57 | Period rules fail | STRICT blocked; BEST_EFFORT manual close with a warning | ✔ |
| E-58 | Period rules technical error | `NOT_ELIGIBLE` until a successful refresh | ✔ |
| E-59 | A file is mid-load when the close runs | Try-lock; the close is deferred | ✔ |
| E-60 | The project's extract job fails after the close | Out of scope (D-76); the closed row and its batch list stay available for a re-run | ✔ |
| E-61 | Crash during the close | One transaction: either every batch closed with the extract, or nothing | ✔ |
| E-62 | Two daily runs of one report period | Separate batches and extracts per run date; a file matches the open one with the latest run date (D-77, D-78) | ✔ |
| E-63 | Late arrival promoted after the close | `Regenerate_Required_Ind = 1`; the project regenerates (D-41) | ✔ |
| E-64 | Regeneration causes resubmission downstream | The project's responsibility | ⚠ Q-16 |
| E-65 | Replacement file arrives while the batch is open and after an early refresh | Re-promote; no regenerate flag, since nothing was closed yet | ✔ |
| E-66 | Run never closed (no data, nobody runs the manual close) | Health query `extracts_past_hold_not_closed` | ⚠ Q-17 |
| E-67 | Illegal status move | `InvalidStatusTransition` (D-69) | ✔ |
| E-68 | `REUSE` requested for a run type with `Carry_Fwd_Ind = 0`, a batch with data, a closed batch, or with no earlier batch with data | `OVERRIDE_INVALID_DETECTED`; batch unchanged (D-70) | ✔ |
| E-69 | File arrives for an open carried-forward batch | O-2: the file wins; `CARRY_FORWARD_REMOVED` | ✔ |
| E-70 | File arrives after a carried-forward batch closed | The batch counts as having data, so it needs a `CORRECTION` override | ✔ |
| E-71 | `REUSE` and a later `CORRECTION` on one batch | Allowed: the index is per (batch, type) (D-02) | ✔ |
| E-72 | Reused batch is corrected later | Its new promoted load changes the extract's data signature → regenerate required (§11.4) | ✔ |
| E-73 | `BATCH_CREATION` names an unknown period, or a period file that does not exist | `ConfigError`, exit 2, nothing is created | ✔ |
| E-68 | Connection pooler in the path | Not allowed (D-55) | ✔ |
| E-69 | Two config rows' templates can match the same name after an edit | Validator blocks the change (`TEMPLATE_OVERLAP`) | ✔ |
| E-70 | Bursts of files beyond job concurrency | Orchestration queue (Phase 2) | ✔ (D-13) |

---

## 14. Feasibility, Drawbacks & Risks

**Feasible as designed:**
- The control plane on RDS Postgres (low volume).
- The in-database core swap.
- Template-regex matching.
- Period-based batch identity.
- Closing the run in the framework and leaving extract generation to the project (D-76), which removes the connector, its retries and its reconciliation.

| Risk / drawback | Impact | Mitigation |
|---|---|---|
| Positional mapping without header-name checks (D-59) | A reordered file with the same column count loads silently into the wrong columns | GRE content rules (type or format checks per column) as the first GATE rules |
| Batches close only with the run (D-39) | A run nobody closes stays open indefinitely; files keep replacing data without approval | Automatic close when eligible; health query `extracts_past_hold_not_closed` (Q-17) |
| The framework does not generate or track the extract (D-76) | A closed run whose extract job failed looks complete here | The project's job chain owns that; the closed row keeps the batch list for a re-run |
| Regeneration after corrections (D-41) | Possible resubmission downstream | Flag only; the project decides (Q-16) |
| One batch and extract per run date (D-77) | A period run daily produces many rows | Intended: each run is its own submission; the health report groups by run date |
| xlsx / large files read in memory (pandas) | Memory limits | `LOAD_ENGINE=SPARK` for that job (D-62); file types Q-02 |
| Spark staging speed | Slow JDBC writes | Bounded partitions, batchsize |
| Session advisory locks | Need direct connections | D-55 |
| Superseded core rows kept forever (D-57) | Storage growth | Monthly partitions; index on current rows |
| New report period | Needs a project period file | `--period-file` takes a project `.py` file; no package deploy (D-71) |
| Manual SQL approvals (D-12) | No maker/checker | `framework_approver` role (D-64), guarded templates, processor validation |
| No database CHECK / exclusion constraints (v6) | A hand-edited row with a bad value is only caught by `validate-config` | Load configuration through the Glue job and run `validate-config` after every change |
| One database for control and data (D-65) | Staging/core volume shares the database with the control tables | Monthly partitions (D-57); separate schemas and roles |
| Job-level settings (D-67, D-71, D-72) | Two jobs of one project could run with different gating / rule modes; settings are not visible in the database | Keep each project's settings in one job definition (Terraform); `show-config` prints every value and its source |
| Carry-forward (D-70) | Reused data may be stale for the new period | Manual approval per batch with an end date (D-74), only for run types that allow it; visible on the batch (`Resolution_Ty = CARRY_FORWARD`, `Reuse_Btch_ID`) and in the audit trail |
| No waivers (v5) | A STRICT run with a permanently missing source needs a person to close it under BEST_EFFORT | The warnings are recorded on the extract row and in the audit trail |
| Two engines (pandas / Spark) | Cast differences between them | Parity tests on the same fixtures |

---

## 15. Package Structure, Tests & Operations

### 15.1 Package
The per-module description (purpose, scope, tables read and written) is in `docs/module-reference.md`.

```
framework/
├── cli.py        commands; --set job arguments
├── app.py        service wiring, health report
├── settings.py   settings + database connection (.env / Secrets Manager) (D-66, D-67)
├── common.py     errors, clock, Btch_ID, Req_Stat values and transitions (§6.1)
├── db.py         init-db, advisory locks (§12.2)
├── config.py     configuration rows, templates (§9), validator (P1)
├── period_sql.py report-period SQL by name (D-71)
├── batches.py    batch creation (P2), intake (P4), CRC / extract rows
├── ingest.py     file pipeline (P5, P6), decision tables (§8)
├── load.py       file reading, staging engines, core swap (§10.2)
├── overrides.py  REUSE decisions and expiry (P7, D-70, D-74)
├── extract.py    eligibility (§11.2), refresh/combine (P9), close (P11), SLA sweep (P10)
├── modules.py    module dispatcher (run --module)
├── audit.py      audit writer, event vocabulary, email notifications (P13)
├── adapters.py   S3 / local store, GRE, email (SES)
└── sql/          schema.sql  approvals.sql
```

**Key contracts** (services take the connection, the clock and the settings):
- `TemplateMatcher.match(object_name) -> MatchResult` (raises `MatchError` with the quarantine code)
- `ingest.decide(ResolutionInput) -> ResolutionDecision` (a pure function)
- `load.swap(conn, cfg, btch_id, load_id, expected_rows, now) -> PromotionResult`
- `RuleEngine.run(conn, bindings, run_params, mode) -> RuleOutcome`
- `extract.compute_eligibility(EligibilityInput) -> Eligibility` (a pure function)
- `ExtractControlService.close(extract_id, closed_by, ack_warnings, automatic) -> CloseOutcome`
- `ExtractEvaluator.run(project_cd, table_nm, run_ty) -> EvaluationSummary`
- `batches.compute_period(conn, name, run_date, lookback_days, lookback_weeks, period_file) -> (start, end)`

### 15.2 Tests
- **Unit:**
  - Template compile/match (every §9.1 rule, including separator and ambiguity rules, and generated-name overlap tests).
  - `resolution_engine` (every §8 row).
  - Eligibility (every §11.2 cell) and the §8 decision tables, including `required_override_ty`.
  - Hold arithmetic (SLA 1 / 2 / N, timezone boundaries).
  - `Btch_ID` / `Seq`, period SQL (month ends, leap day, quarter and year boundaries), the status guard, settings precedence and parameter rendering.
- **Integration** (PostgreSQL with `schema.sql`; Docker optional): one named test per ✔ row in §13; the Appendix C walkthrough replayed with `--as-of`; a negative test for every constraint.
- **Engine parity:** pandas vs Spark on the same fixtures.
- **Concurrency:** ingest vs close, two ingests on one batch, decisions vs ingest.
- **Coverage:** every §8 row, every override path (D-74) and the daily-run grain (D-77); ≥ 90% line coverage of the package.

### 15.3 Operations
- **Alarms:** job-level failure alarms per entry point (Phase 2).
- **Health queries:**
  - stale heartbeats;
  - pending reviews older than N hours;
  - overrides pending review, and approvals expiring within 7 days;
  - extracts past their SLA hold and not closed (Q-17);
  - `Regenerate_Required_Ind = 1`;
  - quarantine counts by reason.
- **Configuration checks:** `framework show-config` (every setting and its source, database target without password) and `framework test-connection`.
- **Security:**
  - SSE-KMS on S3; RDS encryption and TLS; Secrets Manager for DB and API credentials; `.env` only on developer machines and never committed (D-66).
  - DB roles: `framework_app`, `framework_approver` (D-64), `framework_readonly`.
  - No PHI in logs or notifications. ⚠ Q-15: data classification.
- **Retention:** keep everything, with monthly partitions on core, `ComplianceFileLoad` and both audit tables (D-57).
- **Migration:** existing processes move onto the framework by adding config rows and adopting the §9 filename templates. The framework never reads legacy stores (D-17). The plan is ⚠ Q-14.

---

## 16. Open Questions

### 16.1 Resolved in this round
The following v2 questions are closed: O-01, O-02, O-03, O-05–O-08, O-10, O-11, O-13, O-16, O-19, O-20, O-22–O-26, O-31–O-36. See the §2 decisions. Also:
- **O-17 and O-18** were dropped (they were example-specific).
- **O-34** was dropped (it aligned names to sample data).
- **O-21** is answered by D-55, apart from the version (Q-11).
- **O-09** is answered by D-43, D-44 and D-63, apart from the call mechanics (Q-12).
- **O-12** was reduced to Q-02.
- **O-29** was split into D-64 and Q-15.
- **O-04** became Q-01, answered for now by D-69.
- **O-14** was replaced by D-39–D-41.
- **O-15** was answered by D-48.

### 16.2 Still open

| # | Question | Blocks | Designer's proposal (not assumed) |
|---|---|---|---|
| ~~Q-01~~ | *Answered for now by D-69 (fixed list). If the business list differs, change `common.TRANSITIONS`.* | `common.py` | — |
| **Q-02** | Supported file types (`.txt` / `.csv` / `.xlsx` / `.parquet`?). Delimiter, quote and escape characters; encoding; for xlsx, sheet name and header row; trailer record layout and whether its count must equal the data rows. | **`load.py` (file reading)** | csv/txt only in the first release |
| **Q-03** | Filename matching: case-sensitive or not? Allowed characters in `{RUNTY}`? Any other placeholders? Are files only at the inbound prefix root, or also in sub-folders? | **`templates`** | Case-sensitive; `[A-Za-z0-9]`; root only |
| **Q-04** | Which date decides whether a crosswalk row (and its compliance version) is effective: for files, the report start or end date; for scheduled batches, the run date? | `ingest`, `batches` | File: `Rpt_Start_Dt_Key` (`FILE_EFFECTIVE_DATE_BASIS`); scheduled: run date |
| **Q-05** | An ADHOC intake adds a new source to a run whose extract row already exists. The framework raises `Required_Src_Cnt` while the run is open and skips a closed one — is that the business rule? | intake | Allow before the close; skip after |
| ~~Q-06~~ | *Withdrawn in v5: waivers are removed (D-11).* | — | — |
| ~~Q-07~~ | *Withdrawn in v5: the framework does not call the extract job (D-76).* | — | — |
| ~~Q-08~~ | *Withdrawn in v5 with the connectors (D-76).* | — | — |
| ~~Q-09~~ | *Answered in v5: a promotion into a closed run sets `Regenerate_Required_Ind`; the project decides what to do with it (D-41).* | — | — |
| **Q-10** | Zero-byte file when no header is expected: treat it as a zero-record file (D-60 applies)? | `ingest` | Yes |
| **Q-11** | RDS PostgreSQL version | DDL | ≥ 14 (no extensions needed since v6) |
| **Q-12** | GRE call mechanics: import it as a Python library or call a GRE entry point? Naming for rule group and variant per (project, table, source, scope)? Which GRE tables or columns give pass/fail per rule and the completion marker? | **`adapters` (rules engine)** | Library call; group = `project.table`, variant = `src|scope` |
| **Q-13** | Phase-2 orchestration choice (D-13). | Phase 2 only | — |
| **Q-14** | Migration: order of existing processes, parallel-run period and go-live date per project (the first scheduled run defines it). | Rollout | — |
| **Q-15** | Data classification (PHI) per source, KMS keys, and who holds `framework_approver`. | Security | — |
| **Q-16** | Regeneration after a correction may replace an extract already sent externally. Who decides, and how is the downstream told? | project job chain | The project's job chain reads `Regenerate_Required_Ind` and decides |
| **Q-17** | Should runs past their SLA hold and still not closed raise an alert, and after how many days? | ops | Alert after 1 day |
| ~~Q-18~~ | *Withdrawn in v5: `CYCLE_INIT` is gone (D-50, D-79).* | — | — |
| ~~Q-19~~ | *Withdrawn in v4: one database (D-65); GRE receives one connection.* | — | — |

---

## 17. Change Log

**v5 → v6 (schema audit)**
- **Removed table:** `ComplianceEventType` and `sql/seed.sql`; the event vocabulary (category, severity, emailed) is `audit.BATCH_EVENTS` / `audit.AUDIT_EVENTS`. **Added table:** `ComplianceProject` (`Project_Cd`, `Project_Desc`). Still 13 tables.
- **DDL standard:** no `CHECK` constraints, no exclusion constraint and no `btree_gist`; named `pk_` / `fk_` / `uq_` / `ix_` constraints; foreign keys from every `Project_Cd` / `Src_ID` / `Run_Ty`; only the indexes the framework needs; audit columns on every table people maintain.
- **Renamed:** `Src_Cd` → `Src_ID` everywhere (and `src_id` in the GRE `run_params`); `Stg_Tblnm` → `Stg_Table_Nm`; `Effective_Start_Dt` / `Effective_End_Dt` → `…_Dt_Key`; extract `Closed_By` → `Extract_Closed_By`, `Failed_Rule_Refs` → `Failed_Rule_List`; override `Rsn` → `Rsn_Txt`, `Requested_By` → `Created_By`, `Apprvd_By` / `Rejected_By` (+ times) → `Reviewed_By` / `Reviewed_Dtts`; audit `Description` / `Detail_Txt` → `Event_Txt`; setting `DEFAULT_QUARANTINE_URI` → `QUARANTINE_URI`.
- **Removed columns:** file config `Project_Alias`, `Table_Alias`, `Src_Alias` (the template spells them out; `{PROJECT}` / `{TABLE}` / `{SRC}` removed), `Src_File_Ty` (template extension), `Line_Term_Cd`, `Core_Tblnm` (= `Table_Nm`), `S3_Quarantine_Path` (one `QUARANTINE_URI`), `Notify_Channel_Cd`, `Bus_Email_Id`, `Bus_Usr_Grp_Nm`, `Dlvry_Ownr_Grp_Nm`, `Email_Cntnt_Txt`; intake `Req_Ty`, `Rsn`, `Intake_Stat`, `Error_Txt`, `Processed_Dtts`, `Last_Created_Dt_Key`, `Requested_By`, `Requested_Dtts` (→ `Last_Run_Dt_Key`, audit events, `Created_*`; `Intake_ID` is an identity); CRC `Created_By`, `Cmplnc_Vrsn`; extract `Extract_Stat`, `Included_Src_Cds`, `Missing_Src_Cds`, `Carried_Src_Cds`, `Close_Warning_Txt`, `Combine_Run_Cnt`, `Combine_Last_Trigger_Cd`; file load `Received_Dtts`, `Parsed_*`, `Src_Rcd_Cnt`, `Trlr_Rcd_Cnt`, `Core_Appended_Cnt`, `Core_Disabled_Cnt`, `Attempt_Cnt`, `Heartbeat_Dtts`, `Promoted_Dtts`; override copies of the batch grain; audit `Event_Ctgy`; file detail `Entry_Ty`, `Received_File_Ref`.
- **Changed:** every notification is an email (SES), SNS removed (D-54); intake has no status — a request is due while its window is open and is handled once per run date (D-35, D-79); `validate-config` checks the value rules the database no longer enforces.

**Post-v5 implementation notes (no schema or decision change)**
- **`ingest-path` (§3, §7 P5b):** a thin sweep over `process_file` that lists every object at one inbound location, or at every active file config's location, and processes each one through the unchanged §8 resolution logic. No new table, column or decision; D-26 (one file per batch) and D-33 (one file config per project/table/source) are unaffected because every object is still resolved individually.
- **`app.py` carries no table-specific SQL.** `health()` (§15.3) now composes its report from `ingest.IngestPipeline.health()`, `overrides.DecisionProcessor.health()` and `extract.ExtractControlService.health()` instead of querying those tables itself, matching §15.1's per-module table ownership. The report's shape (keys and columns) is unchanged.

**v4 → v5 (simpler control tables)**
- **Removed table:** `ComplianceExtractTrigger` (D-76). 14 → 13 tables. The framework closes the run; the project's job chain generates the extract.
- **Removed columns:** CRC `Earliest_Close_Dt` (computed, D-77), `Intake_ID` (D-79), `Current_Load_ID` (D-75), `Closed_By_Trigger_ID`; extract `Earliest_Trigger_Dt`, `Waived_Src_Cnt`, `Waived_Src_Cds`, `Trigger_Stat`, `Last_Trigger_ID`, `Trigger_Cnt`, `Retrigger_Required_Ind`, `Triggered_Data_Signature`, `Triggered_Combine_Run_Nbr`; override `Extract_ID`, `Candidate_Load_ID`, `Reviewed_Load_ID`, `Prior_*`, `Rule_Ref`, `Promotion_Stat`, `Promoted_Dtts`, `Last_Processed_Apprvl_Stat`.
- **Removed override types and states:** `SOURCE_WAIVER`, `RULE_WAIVER` (D-11, D-48) and `REVOKED` (D-74). The reopen types became `LATE_ARRIVAL` and `CORRECTION`, and `CARRY_FORWARD` became `REUSE`.
- **Added:** `Req_Dt_Key` in the CRC and extract grain (D-77); extract `Closed_By`, `Close_Warning_Txt`, `Closed_Data_Signature`, `Regenerate_Required_Ind`; override `Valid_Thru_Dt_Key` and `Rsn`; intake `Req_Start_Dt_Key`, `Req_End_Dt_Key`, `Last_Created_Dt_Key` and the `IN_PROGRESS` state (D-79); the partial unique index `ux_fileload_promoted` (D-75).
- **Changed:** the SLA hold is computed from `Req_Dt_Key + (SLA_Days − 1)` and never stored; a file is matched to the open batch with the latest run date, and a quarantine because "every batch is closed" is retryable (D-78); intake is ADHOC-only with a request window and free-form `Req_Ty` (D-79); `schema.sql` is `CREATE`-only (review phase, no `ALTER`).
- **Removed commands:** `trigger-extract` and `resolve-trigger` → `close-extract --extract-id --closed-by [--ack-warnings]`.
- **Removed settings:** every `EXTRACT_JOB_*`, `EXTRACT_ENDPOINT_URL`, `EXTRACT_PARAMS`, retry, reconcile and waiver setting; added `AUTO_CLOSE_EXTRACTS`.
- **Removed code:** Glue and HTTP extract connectors, the trigger service, archive re-staging (D-53), the correction-flag view and the `CYCLE_INIT` / `CORRECTION_REQUEST` intake types.

**v3.2 → v4 (simplification)**
- **Removed tables:** `ComplianceRequestStatus`, `ComplianceRequestStatusTransition` (D-69), `CompliancePeriodStrategy` (D-71), `ComplianceDbConnection` (D-66), `ComplianceFrameworkSetting` (D-67), `ComplianceExtractPolicy`, `ComplianceExtractJobParam` (D-72). 20 → 14 tables.
- **Removed columns:** crosswalk `Period_Strategy_Cd`, `Lookback_Days`, `Lookback_Weeks`, `Schedule_Cron_Expr`, `Business_Tz`; file config `Target_Connection_Nm`, `Engine_Cd`, `Rules_Vld_Md`, `Is_Rules_Engine_Required`, `Load_Exclude_Col_List`, `Sns_Topic_Arn` (D-73).
- **Added:** `ComplianceRunType.Carry_Fwd_Ind`; override type `CARRY_FORWARD` with `Reuse_Btch_ID`; CRC `Reuse_Btch_ID`, `Resolution_Ty = CARRY_FORWARD`, `Req_Stat = CARRIED_FORWARD`; `ComplianceExtractControl.Carried_Src_Cds`; `ComplianceExtractTrigger.Extract_Job_Ref` (D-70, D-72).
- **Changed:** one database for metadata, staging and core, so promotion is one transaction (D-65, D-68); connection from `.env` or Secrets Manager (D-66); settings from job arguments / environment / `.env` (D-67); report period named per scheduled job (`period_sql.py`, `--period-file`) (D-71); extract job, parameters and gating mode per project job (D-72); file rules run when bindings exist; GRE entry point receives one connection.
- **Removed commands:** `catchup` (re-run `create-batches --as-of`); `test-connections` became `test-connection`.
- **Package:** 55 Python files in 13 sub-packages → 15 modules; 15 SQL files → 3 (`schema.sql`, `seed.sql`, `approvals.sql`); `framework.ini` → `.env`; `croniter` dependency dropped.
- **Docs:** the superseded v1/v2 drafts (`framework-master.md`, `orchestration-flow.md`, `reuse-and-late-arrival.md`, `schema-design.md`) and their interactive pages were removed; they remain in git history.

**v3.2**
- **Added** `ComplianceDbConnection`, `ComplianceFrameworkSetting` and `ComplianceSourceFileConfig.Target_Connection_Nm` (D-65 – D-67).
- **Changed:** the metadata schema name and connection are configurable; the DDL is schema-unqualified; promotion across two databases uses commit ordering plus an idempotent swap (D-68); the GRE entry point receives `(data_conn, metadata_conn, …)`.
- **Added commands:** `show-config`, `test-connections`.

**v2 → v3**

**Removed**
- **Carry-forward, entirely** (D-34): anchor override type, expiry job, `Used_Btch_ID`, `Reuse_Valid_Thru_Dt_Key`, `CARRIED_FORWARD` resolution, the carry-forward grouping index and all anchor edge cases.
- **SLA-driven batch close** (replaced by trigger-driven close, D-39).
- **`Intake_ID` in the grain.**
- **`Req_Dt_Key` as batch identity** (it is now the creation date).
- **All worked examples, mock data, legacy inventory and sample-code references** (per your instruction).

**Changed**
- **CRC grain** is now report-period based (D-30).
- **Extract grain** is now report-period based.
- **File config:** one row per (project, table, source), with a filename template and aliases.
- **GATE/ANNOTATE** moved to the file config (D-44).
- **Period-level mode** moved to the policy (D-63).
- **Status logic:** written against abstract states until Q-01 is answered.
- **Crosswalk key:** now includes `Effective_Start_Dt`, with a non-overlap constraint (D-51).

**Added**
- **Filename template engine** (§9).
- **SLA hold** (D-38).
- **Extract eligibility** (§11.2).
- **Extract trigger and connector framework** (§11.3, `ComplianceExtractTrigger`, `ComplianceExtractJobParam`).
- **Re-trigger after reopen** (D-41).
- **Re-staging from the archive** (D-53).
- **Zero-record flag** (D-60).
- **Engine per config** (D-62).
- **Notification channel config** (D-54).

---

## Appendix A: PostgreSQL DDL (tested on PostgreSQL 16)

The DDL is maintained in one place: [`src/framework/sql/schema.sql`](../../src/framework/sql/schema.sql). `framework init-db` applies it (once) into the metadata schema, and the Glue metadata-load deployment uploads the same file for reference. §5 describes every table; the end of the file documents the framework columns that each staging and core table needs.

---

## Appendix B: Override SQL Templates (D-12, D-64, D-74)

The templates are maintained in [`src/framework/sql/approvals.sql`](../../src/framework/sql/approvals.sql). Run them as `framework_approver`, with `search_path` set to the metadata schema (D-65). **Each must report 1 row.** A result of 0 means the row was already decided, or the batch is not in the state the template requires.

| # | Template | Guard |
|---|---|---|
| 1 | Insert an approved `REUSE` (optional `:reuse_btch_id`, required `:valid_thru`) | batch open, no promoted load |
| 2 | Insert an approved `LATE_ARRIVAL` | batch closed without data |
| 3 | Insert an approved `CORRECTION` | batch closed with data |
| 4 | Approve a `PENDING_REVIEW` row (sets `Valid_Thru_Dt_Key`) | `PENDING_REVIEW` |
| 5 | Reject a `PENDING_REVIEW` row | `PENDING_REVIEW` |
| 6 | Stop an approved override early | move `Valid_Thru_Dt_Key` into the past — there is no revoke |

---

## Appendix C: Synthetic Walkthrough

All names are placeholders.

**Config**
- Project `PRJA`, table `tbl_x`, run type `MONTHLY` (`SLA_Days = 2`, `Carry_Fwd_Ind = 1`).
- Scheduled jobs: `run --module BATCH_CREATION --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH` at 06:00 on the 1st; `evaluate-extracts --project PRJA` every 15 minutes with `EXTRACT_GATING_MODE=STRICT_ALL_PASS`.
- Sources `S1` and `S2`, with templates `PRJA_TBLX_S1_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt` and `PRJA_TBLX_S2_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt`.

| When | Event | Result |
|---|---|---|
| Feb 1 06:00 | `BATCH_CREATION` runs; period Jan 1–31 | Two batches: `20260201_PRJA_tbl_x_S1_MONTHLY_<v>_1` and `…_S2_…_1`. Extract row created for run date Feb 1 with `Required = 2`; its hold ends Feb 2. |
| Feb 1 09:30 | `PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt` | Matched to the open Feb 1 batch; promoted (O-1). Extract `PARTIAL`. |
| Feb 1 11:00 | S2 file | Promoted. Complete → early combine → period rules `PASSED`. Eligibility `NOT_ELIGIBLE` (hold until Feb 2). |
| Feb 1 12:00 | S1 resend with new `{TS}` and new content | O-2 replacement; the prior load is superseded; early combine re-runs. |
| Feb 2 00:15 | `evaluate-extracts` | Hold passed; complete; rules passed → `AUTO` → run closed: both batches `COMPLETED`, `Extract_Close_Ind = 1`, `Combine_Btch_ID_List` frozen. The project's extract job runs next in the chain. |
| Feb 5 | Corrected S2 file, no override | C-3: quarantined `FILE_REJECTED_BATCH_CLOSED`. |
| Feb 5 | Analyst inserts an approved `CORRECTION` (template 3, `Valid_Thru_Dt_Key = Feb 10`) and the file is delivered again | C-2: the same `Load_ID` is reprocessed and promoted; the batch stays closed; `Regenerate_Required_Ind = 1`. |
| Feb 6 | `PRJA_TBLX_S3_MONTHLY_…` (no template for S3) | C2 `FILE_REJECTED_UNPARSEABLE`. |
| Feb 6 | `PRJA_TBLX_S1_MONTHLY_20260201_20260228_…` (February batch not created yet) | C6 `FILE_REJECTED_NO_BATCH`. |
| Mar 1 06:00 | `BATCH_CREATION`; period Feb 1–28 | February batches for S1 and S2, run date Mar 1. |
| Mar 1 | S1 February file | Promoted. S2 has nothing. |
| Mar 1 | Analyst inserts an approved `REUSE` for S2 (template 1, valid through Mar 31) | `process-decisions`: S2 February → `CARRIED_FORWARD`, `Reuse_Btch_ID` = S2 January batch. Extract complete (S2 carried); combine reads S1 February + S2 January rows. |
| Mar 2 00:15 | `evaluate-extracts` | `AUTO` → run closed; `Combine_Btch_ID_List` names S1 February and S2 January; S2 February closes `COMPLETED` / `CARRY_FORWARD`. |
| Mar 3 | S2 February file arrives | The batch is closed and counts as having (carried) data, so it needs a `CORRECTION` override; with one, C-2 promotes it, `Reuse_Btch_ID` is cleared and the run is marked for regeneration. |

**Ad-hoc window (D-79).** An intake row for run type `ADHOC`, report period Feb 1–10, `Req_Start_Dt_Key = Feb 11`, `Req_End_Dt_Key = Feb 20` makes the daily `BATCH_CREATION` run create one batch (and one extract) per run date for ten days, all for the same report period. Each of those runs closes on its own SLA hold.
