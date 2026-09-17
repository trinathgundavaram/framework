# CMS Compliance Framework: Consolidated Design (v4)

| | |
|---|---|
| **Status** | Build-ready for every module that §16 (Open Questions) does not name as blocked. |
| **Revision** | **v4, 2026-09-17: simplification (D-69 – D-73).** Six configuration tables removed (status, period strategy, DB connection, framework setting, extract policy, extract job parameter); connections, settings, report periods and extract jobs are job-level; one database for metadata, staging and core; crosswalk and file config trimmed; carry-forward reintroduced as a per-batch approval for run types that allow it (D-70); the package collapsed into 15 modules. v3.2, 2026-09-16: configurable metadata database and schema, named data-database connections, metadata-driven runtime settings (D-65 – D-68). v3.1, 2026-09-16 (implementation notes added: Appendix A columns, extra operational events; code in `src/framework`). v3, 2026-09-16. Replaces v2 and v1. v3 is **project-agnostic** and **filename-driven**. Carry-forward is removed, batches are keyed by report period, and batches close only when the framework triggers the extract. See §17. |
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
11. Extract Trigger & Batch Close
12. Idempotency & Concurrency
13. Edge Cases & Negative Scenarios
14. Feasibility, Drawbacks & Risks
15. Package Structure, Tests & Operations
16. Open Questions
17. Change Log
- Appendix A: PostgreSQL DDL (tested on PostgreSQL 16)
- Appendix B: Approval / Waiver SQL Templates
- Appendix C: Synthetic Walkthrough

---

## 1. Purpose & Scope

A single, **project-agnostic** framework for compliance source files. It does the following:
- Registers expected batches.
- Recognises incoming files purely from **filename templates stored in config**.
- Validates, stages and promotes the data to core.
- Handles late arrivals and corrections through human approval.
- Runs cross-source period validation.
- **Triggers an external extract job or API**, then closes the batches.

**Adding a project, table, source or run type is config only** (D-27). The package has no project-specific code paths, names or branches. Scheduling a project is job configuration, not code: the report period is a named SQL statement (`period_sql.py`, or a project file passed with `--period-file`), and the extract job, its parameters and the gating mode are arguments of the project's job (D-71). A new extract-job *type* (the connector) still needs a package deploy.

**In scope:** config and validation; batch creation (scheduled, re-run for a missed date, on-demand cycle, ad-hoc); file intake (template match, dedupe, quarantine); staging; file-level validation (UMcM GRE); core promotion; reopen approvals; waivers; combine and period-level validation; extract eligibility; **calling** the extract job/API; batch close; audit and notifications.

**Out of scope:**
- Generating the extract itself (D-39). The framework only calls the configured job/API and does not track its completion.
- An approval UI (D-12).
- AWS orchestration wiring, which is deferred (D-13).

---

## 2. Decision Register

### 2.1 Active decisions

| ID | Decision |
|---|---|
| D-01 | **Promotion (core load).** In one transaction, set `Current_Ind = 0` on the current core rows with the same `Btch_ID`, then append the newly staged rows. |
| D-02 | **Override keys.** At most one active reopen override per batch (`Req_ID`). At most one active `SOURCE_WAIVER` **or** `CARRY_FORWARD` per batch. At most one active `RULE_WAIVER` per (extract, rule). |
| D-03 | **Batch and load identity.** `Btch_ID` is fixed at batch creation and never changes. Every physical file gets its own `Load_ID`, which is stamped everywhere its data or events appear. |
| D-04 | **Reopened batches stay closed.** A closed batch that gets an approved reopen stays closed (`Batch_Close_Ind = 1`); promotion runs under the approval. |
| D-05 | **Staging.** Staging is never truncated. Before loading, the framework deletes only `WHERE Btch_ID = :btch_id`. |
| D-08 | **Rules engine.** File-level and period-level validation use UMcM GRE. |
| D-09 | **Extract grain includes `Run_Ty`.** |
| D-11 | **Waivers.** STRICT deadlocks are released by manual waivers (`SOURCE_WAIVER`, `RULE_WAIVER`). |
| D-12 | **Approvals.** Approvals, rejections and waivers are manual SQL for now; a UI comes later. |
| D-13 | **AWS orchestration** (Glue triggers vs Step Functions) is decided later. The package is orchestration-agnostic. |
| D-14 | **Replacement while open.** A new file for an open batch replaces the previous one: same `Btch_ID`, new `Load_ID`, no approval. |
| D-15 | **No batch for the period.** A file whose (project, table, source, run type, report period) has no batch is quarantined immediately. |
| D-17 | **Independence.** The framework runs independently of existing stores and processes; existing processes migrate onto it. Column names are framework-owned. |
| D-19 | **GATE failures.** A file-level GATE failure rejects the whole file. |
| D-21 | **Early combine.** Combine and period rules also run as soon as every required source has data. |
| D-25 | **`Table_Nm`** is the physical core table name. |
| D-26 | **One file per batch.** One file per source per batch; any sub-type split lives inside the data. |
| D-27 | **Project-agnostic.** All behaviour comes from config tables; the code has no project-specific logic. |
| D-28 | **Filename templates.** Each file-config row stores a filename **template** with placeholders `{PROJECT}`, `{TABLE}`, `{SRC}`, `{RUNTY}`, `{RPTSTART}`, `{RPTEND}`, `{TS}` and literal text (§9). |
| D-29 | **`Req_Dt_Key`** is the **actual date the batch was created**. It is embedded in `Btch_ID` and is **not** in filenames. A batch created late (job re-run with `--as-of`) uses the date of that run. |
| D-29b | **Late batches.** A missed scheduled run is recreated by running `create-batches --as-of <missed date>`; the report period is computed from that date (D-71). |
| D-30 | **One batch per period.** Exactly **one batch per (Project, Table, Source, Run type, Report start, Report end)**, including ADHOC. This is the CRC grain. |
| D-31 | **Filename tokens.** `{PROJECT}`, `{TABLE}` and `{SRC}` must equal **aliases stored on the file-config row**. `{RUNTY}` must equal a `Run_Ty` code exactly. |
| D-32 | **Date format.** Filename dates are `YYYYMMDD`. |
| D-33 | **One file-config row per (Project, Table, Source).** `{RUNTY}` selects the run type, which must be configured for that source. |
| D-34 | **No automatic carry-forward.** A batch without usable data at close is `MISSING`, unless a carry-forward was approved for it (D-70). |
| D-35 | **Duplicate ad-hoc intake.** A second ADHOC intake for a (table, source, period) that already has a batch is rejected (`Intake_Stat = FAILED`). |
| D-36 | **`{TS}`.** Filenames carry a unique token `{TS}` = `YYYYMMDDHHMMSS`. |
| D-37 | **Latest arrival wins.** `{TS}` only makes names unique and is not used for ordering. |
| D-38 | **SLA hold.** `SLA_Days` is a **minimum hold** before a batch may close. A batch may close from the start of `Req_Dt_Key + (SLA_Days − 1)` calendar days: SLA 1 = the creation day, SLA 2 = any time the next day, and so on. |
| D-39 | **Closing a batch.** Batches close **only** when the framework successfully **calls the configured extract job/API** (with configured parameters). The status is then set to request complete. Extract generation itself, and its outcome, are outside the framework. |
| D-40 | **Triggering the extract.** Triggering is **automatic** when the period is fully eligible (all sources have data, period rules passed, SLA hold over). It is **manual** (command) otherwise. **BEST_EFFORT:** manual trigger allowed any time after the SLA hold, with warnings. **STRICT_ALL_PASS:** allowed only when all sources and rules succeed, **except** through manual approval (waivers). |
| D-41 | **Re-trigger after reopen.** After a closed batch's reopen is promoted, combine and rules re-run and the extract is **re-triggered automatically** under the same eligibility rules. |
| D-42 | **Extract job config.** The extract job/API and its **parameters are configurable** per project job (D-72), including extra job-specific parameters. |
| D-43 | **GRE location.** GRE metadata and results live in the **same Postgres database** as the framework. |
| D-44 | **GATE/ANNOTATE** for file rules is a job setting (`FILE_RULES_MODE`, D-73). File rules run whenever the source has FILE_LEVEL rule bindings. |
| D-45 | **Reopen file fails GATE.** The file is rejected; no override is created; an alert is raised. |
| D-46 | **Replacement fails GATE while open.** The prior promoted data stays current; the batch goes to `EXCEPTION_PENDING`. |
| D-47 | **Second correction.** A second correction on a batch whose reopen was `LATE_ARRIVAL_REOPEN` keeps that type (update in place). |
| D-48 | **Waiver mechanics.** Waivers are a manual SQL insert followed by a manual SQL approval. `RULE_WAIVER` is per rule. A waiver can be revoked until the extract is triggered. |
| D-49 | **BEST_EFFORT with no data.** No minimum-data requirement; a zero-data trigger is allowed with a warning. |
| D-50 | **`CYCLE_INIT`.** An intake with `Req_Ty = CYCLE_INIT` creates the routine batches for a named period on demand. |
| D-51 | **`Cmplnc_Vrsn`.** An attribute on **effective-dated** crosswalk rows. A new version end-dates the old row and adds a new one. |
| D-52 | **Same content, different batch.** Content identical to another batch's file is allowed and logged as a warning. |
| D-53 | **Missing staged rows.** If an approved reopen's staged rows are gone at promotion, the file is **re-staged from the S3 archive** (checksum verified). |
| D-54 | **Notifications.** Sent via SES and/or SNS; the channel is configurable per event type and per file-config row; the SNS topic is a job setting (`SNS_TOPIC_ARN`). |
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
| D-69 | **Fixed `Req_Stat` list (v4, answers Q-01 for now).** `PENDING`, `PROMOTED`, `CARRIED_FORWARD`, `EXCEPTION_PENDING` (open); `COMPLETED`, `COMPLETED_WITH_EXCEPTION`, `DATA_NOT_PROVIDED` (closed). The values are a CHECK constraint on CRC; the legal transitions are in code (`common.TRANSITIONS`). The status and transition tables are removed. |
| D-70 | **Carry-forward by approval (v4).** `ComplianceRunType.Carry_Fwd_Ind = 1` allows an **open batch without data** to reuse the data of the latest earlier **closed batch with data** of the same (project, table, source, run type), after a manual `CARRY_FORWARD` override is approved (optionally naming `Reuse_Btch_ID`). The batch becomes `CARRIED_FORWARD` / `Resolution_Ty = CARRY_FORWARD`, counts as received, and the combine reads the reused batch's current core rows (no data is copied). A file arriving before close replaces it; revoking it before the trigger returns the batch to `PENDING`; a file after close is a `LATE_ARRIVAL_REOPEN`. |
| D-71 | **Report period is a job parameter (v4).** The crosswalk no longer holds period strategy, lookback, cron or time zone. The project's scheduled job runs `create-batches --project --run-type --period <NAME>`; `<NAME>` is a statement in `period_sql.py` (or in a project `.py` file passed with `--period-file`). The run date is today in `BUSINESS_TZ` or `--as-of`. `CompliancePeriodStrategy`, cron expansion and the `catchup` command are removed. |
| D-72 | **Extract job at job level (v4).** `ComplianceExtractPolicy` and `ComplianceExtractJobParam` are removed. The project's `evaluate-extracts` / `trigger-extract` job carries `EXTRACT_JOB_TYPE`, `EXTRACT_JOB_NAME` or `EXTRACT_ENDPOINT_URL`, retries, `EXTRACT_GATING_MODE`, `PERIOD_RULES_MODE` and `EXTRACT_PARAMS` (JSON name → text with placeholders). The job called is recorded on each trigger (`Extract_Job_Ref`). |
| D-73 | **Leaner configuration rows (v4).** The crosswalk only says which (project, table, source, run type) apply and when (plus `Cmplnc_Vrsn`). The file config keeps the file contract, locations, targets and notification recipients; `Target_Connection_Nm`, `Engine_Cd`, `Rules_Vld_Md`, `Is_Rules_Engine_Required`, `Load_Exclude_Col_List` and `Sns_Topic_Arn` are removed. |

Also carried from v1: gating modes `STRICT_ALL_PASS` / `BEST_EFFORT` (a job setting since v4); extract generation is external; notification recipient lists are comma-delimited text; the local package is built first.

### 2.2 Withdrawn
All of these were withdrawn by D-34, D-30 or D-39:
- **v2 carry-forward anchors (D-34):** D-06 (revoke → candidate), D-18 (`Used_Btch_ID` = anchor), D-22 (carry-forward vs reopen), D-24 (anchor expiry). D-23 (carry-forward counts as received) returns in D-70 for approved carry-forwards only.
- **v3.2 configuration (v4):** the metadata/data database split, `ComplianceDbConnection`, `ComplianceFrameworkSetting`, `framework.ini`, cross-database promotion replay.
- **Grain change (D-30):** D-10 (`Intake_ID` in the grain).
- **Close change (D-39 / D-38):** D-16 (SLA closes batches).
- **Minimum data (D-49):** D-20 (BEST_EFFORT needs ≥1 source).
- **Filename rules (D-28–D-37):** D-07 (filename tokens, now superseded).

---

## 3. Architecture & Build Phasing

**One package, thin callers.** The `framework/` package holds all logic and has no AWS-orchestration imports. Entry points only parse arguments, call one service and set the exit code. Every time-based command takes `--as-of`; nothing inside calls "today" directly.

**Phase 1 (build now, local: pytest + PostgreSQL, Docker optional):**

| CLI command | Service | Eventual trigger (D-13) |
|---|---|---|
| `init-db` | `db.init_db` (schema once, event vocabulary) | deploy |
| `show-config` / `test-connection` | `settings` (D-66, D-67) | deploy / ops |
| `validate-config` | `config.validate_all` | CI, and before any config change is applied |
| `create-batches --project --run-type --period [--as-of]` | `batches.create_batches` | project schedule (D-71) |
| `process-intake` | `batches.IntakeProcessor` (CYCLE_INIT, ADHOC, CORRECTION) | poll |
| `ingest-file --bucket --key [--version-id]` | `ingest.IngestPipeline.process_file` | S3 event |
| `process-decisions` | `overrides.DecisionProcessor` | poll (every few minutes) |
| `evaluate-extracts [--project]` | `extract.ExtractEvaluator` (refresh + auto-trigger sweep) | project schedule, every 15 min (D-72) |
| `trigger-extract --extract-id --requested-by [--ack-warnings]` | `extract.ExtractTriggerService.fire` | human |
| `refresh-extract --extract-id` | `extract.ExtractControlService.refresh` | chained / human |

Every command takes `--set NAME=VALUE` job arguments, so one job definition per project carries that project's period, extract job and gating mode.

**Phase 2 (deferred):** wrap the same services in Glue or Step Functions. Constraints for Phase 2:
- The file pipeline needs a dedicated DB session for its whole run, because it holds session advisory locks (§12).
- Approvals are detected by polling.
- S3 bursts must be queued beyond the job concurrency limit.

---

## 4. Core Concepts & Glossary

| Term | Definition |
|---|---|
| **Batch (CRC row)** | One per `(Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)` (D-30). Current-state; updated in place; never duplicated. |
| **Report period** | `Rpt_Start_Dt_Key..Rpt_End_Dt_Key`. Scheduled batches get it from the named period SQL of the project's job, computed from the run date (D-71). CYCLE_INIT and ADHOC batches get it from the intake. Files carry it in the name. |
| **`Req_Dt_Key`** | The actual batch creation date (D-29), used in `Btch_ID` and in the SLA hold. |
| **`Btch_ID`** | `{Req_Dt_Key:YYYYMMDD}_{Project_Cd}_{Table_Nm}_{Src_Cd}_{Run_Ty}_{Cmplnc_Vrsn}_{Seq}`. `Seq` = 1 + the number of batches already created on that `Req_Dt_Key` for the same (project, table, source, run type), computed under lock. Seq is needed because a re-run for a missed date or CYCLE_INIT can create several periods on one day. Unique; never changes. |
| **`Load_ID`** | One physical file (`ComplianceFileLoad`). Separates the original file, replacements and corrections within the same `Btch_ID`. |
| **Resolution** | `NEW_FILE` (the batch has a promoted load), `CARRY_FORWARD` (an approved carry-forward reuses an earlier batch's data, D-70), `MISSING` (closed with no usable data), or `NULL` (open, no usable data yet). |
| **SLA hold** | `Earliest_Close_Dt = Req_Dt_Key + (SLA_Days − 1)`. The batch cannot close before the start of that calendar day in `BUSINESS_TZ` (D-38). |
| **Extract grain** | `(Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)`. All sources' batches for the same period roll up into one row. |
| **Eligibility** | Whether an extract may be triggered, and whether that happens automatically or manually (§11). |
| **Trigger** | A framework call to the configured extract job/API. When the call is accepted, every batch in the grain closes (D-39). |
| **Reopen** | A valid file for a closed batch. It needs approval before promotion (D-04). |
| **Waiver** | An approved override that treats a missing or failed source (`SOURCE_WAIVER`) or a failed period rule (`RULE_WAIVER`) as satisfied for STRICT eligibility. |
| **Carry-forward** | An approved override (`CARRY_FORWARD`) that lets an open batch without data reuse an earlier batch's data; only for run types with `Carry_Fwd_Ind = 1` (D-70). |
| **Job setting** | A value passed to a scheduled job (`--set`), or read from the environment / `.env` (D-67). |

---

## 5. Data Model

The DDL is `src/framework/sql/schema.sql` (Appendix A).
- **Schema:** configurable metadata schema (default `cms_compliance`, D-65). The DDL is unqualified and applied with `search_path` set to that schema. PascalCase names are unquoted, so Postgres folds them to lowercase.
- **Database:** one PostgreSQL database holds the framework tables and the staging/core tables (D-65).
- **14 tables:** 5 configuration (+ the event vocabulary), 5 control, 2 audit, 1 trigger log. Removed in v4: `ComplianceRequestStatus`, `ComplianceRequestStatusTransition`, `CompliancePeriodStrategy`, `ComplianceDbConnection`, `ComplianceFrameworkSetting`, `ComplianceExtractPolicy`, `ComplianceExtractJobParam`.
- **Types:** timestamps are `TIMESTAMPTZ` (UTC). Indicators are `SMALLINT` 0/1.
- **Audit columns:** every config table carries `Created_*` / `Updated_*`.

### 5.1 Configuration

| Table | Key | Purpose / rules |
|---|---|---|
| `ComplianceSourceSystem` | `Src_Cd` | Source master. |
| `ComplianceRunType` | `Run_Ty` | `Run_Category_Cd` (`ROUTINE` / `ADHOC`). `SLA_Days ≥ 1` (hold, D-38). `Carry_Fwd_Ind` (D-70). |
| `ComplianceDataSetSourceXwalk` | `(Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt)` | Which (project, table, source, run type) combinations apply and when: `Cmplnc_Vrsn`, `Active_Ind`, effective window. **Effective windows for the same 4-part key cannot overlap** (GiST exclusion constraint, D-51). Nothing about schedules, periods or time zones (D-71). |
| `ComplianceSourceFileConfig` | `Cfg_ID`; one active row per `(Project_Cd, Table_Nm, Src_Cd)` (D-33) | The file contract (below). |
| `ComplianceRuleBinding` | `(Project_Cd, Table_Nm, Src_Cd, Rule_Scope_Cd, Gre_Rule_Group, Gre_Rule_Variant)` | Links a scope to GRE rules. `Src_Cd = '*'` means all sources, used for `PERIOD_LEVEL`. A source with no FILE_LEVEL binding skips file rules. The GATE/ANNOTATE modes are job settings (D-44, D-63). ⚠ Q-12 (GRE call details). |
| `ComplianceEventType` | `Event_Ty` | Event vocabulary: log table, category, severity, `Notify_Ind`, `Notify_Channel_Cd` (D-54). Seeded by `init-db`. |

**Job-level configuration (not tables, D-66 – D-72)**

| What | Where |
|---|---|
| Database connection | `.env` locally; Secrets Manager (`FRAMEWORK_DB_SECRET_NAME`) in AWS |
| Runtime settings | `--set` job arguments > environment > `.env` > defaults (`settings.py`) |
| Report period | `create-batches --period <NAME>` (`period_sql.py` or `--period-file`), `--lookback-days/weeks` |
| Business time zone | `BUSINESS_TZ` |
| Extract job, parameters, retries | `EXTRACT_JOB_TYPE`, `EXTRACT_JOB_NAME` / `EXTRACT_ENDPOINT_URL`, `EXTRACT_PARAMS`, `EXTRACT_MAX_CALL_RETRIES`, ... |
| Gating and rule modes | `EXTRACT_GATING_MODE`, `PERIOD_RULES_MODE`, `FILE_RULES_MODE` |
| Load engine | `LOAD_ENGINE` |
| `Req_Stat` values | CHECK constraint on CRC; transitions in `common.TRANSITIONS` (D-69) |

**`ComplianceSourceFileConfig` columns**

| Group | Columns |
|---|---|
| Identity | `Project_Cd`, `Table_Nm`, `Src_Cd` |
| Filename | `Src_File_Nm_Tmplt` (D-28), `Project_Alias`, `Table_Alias`, `Src_Alias` (D-31) |
| File format | `Src_File_Ty`, `Delmtr_Cd`, `Line_Term_Cd`, `Src_File_Has_Hdr_Ind`, `Src_File_Has_Trlr_Ind` (⚠ Q-02) |
| Handling | `Allow_Zero_Rcd_Ind` (D-60) |
| S3 paths | inbound, archive, quarantine |
| Targets | staging schema and table; core schema and table (`Core_Tblnm = Table_Nm`, D-25) — same database |
| Notifications | business and delivery-owner groups, success/failure recipient lists, subject/body text, `Notify_Channel_Cd` |

**`config.validate_all` rejects:**
- Templates missing any required placeholder, using a placeholder twice, or using an unknown placeholder (§9.1); a template that does not end with the file type.
- Two active configs that could match the same filename (§9.3), or that share aliases.
- A file config with no active crosswalk row, or an active crosswalk row with no file config.
- Staging / core tables missing or lacking framework columns.
- Inbound / archive / quarantine paths that are not `s3://` URIs.
- Missing event types (run `init-db`).
- It warns about crosswalk rows whose run type is inactive.
- Job-level values are checked when they are used: an unknown period name, a missing lookback, an unknown `EXTRACT_PARAMS` placeholder or a missing extract job raise `ConfigError` and the command exits 2.

### 5.2 Control

**`ComplianceRequestControl` (CRC)**, one row per batch:

| Column | Notes |
|---|---|
| `Req_ID` | Identity PK |
| `Project_Cd`, `Table_Nm`, `Src_Cd`, `Run_Ty`, `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | **Unique grain (D-30)** |
| `Req_Dt_Key` | Actual creation date (D-29) |
| `Earliest_Close_Dt` | `Req_Dt_Key + SLA_Days − 1`, stored at creation (D-38) |
| `Btch_ID` | Unique, immutable (D-03) |
| `Cmplnc_Vrsn` | From the crosswalk row effective at creation |
| `Intake_ID` | The intake that created the batch (CYCLE_INIT / ADHOC); null for scheduled batches. **Not part of the grain.** |
| `Req_Stat` | Fixed list (D-69, §6.1) |
| `Resolution_Ty` | `NEW_FILE` / `CARRY_FORWARD` / `MISSING` / NULL |
| `Current_Load_ID` | The load whose rows are current in core |
| `Reuse_Btch_ID` | `CARRY_FORWARD` only: the batch whose current data is reused (D-70) |
| `Batch_Close_Ind`, `Closed_By_Trigger_ID` | Close state (D-39) |
| `Created_By` | `SCHEDULER` / `CYCLE_INIT` / `ADHOC_INTAKE` |
| `Created_Dtts`, `Updated_Dtts` | Audit timestamps |

CHECK constraints:
- `NEW_FILE` requires `Current_Load_ID`.
- `MISSING` requires no current load.
- `CARRY_FORWARD` requires `Reuse_Btch_ID` and no current load; `Reuse_Btch_ID` is set only for `CARRY_FORWARD`.
- A closed batch must be resolved and must reference its trigger.
- `Earliest_Close_Dt ≥ Req_Dt_Key`.
- `Rpt_End ≥ Rpt_Start`.

**`ComplianceFileLoad`**, one row per physical S3 object, including quarantined files:

| Group | Columns |
|---|---|
| S3 object | bucket, key, version id, ETag, size, SHA-256, received time |
| Parsed tokens | project/table/source/run-type/report dates, `Parsed_File_Ts` |
| Links | `Cfg_ID`, `Req_ID`, `Btch_ID` |
| Status | `Load_Stat`, `Rules_Stat`, `Quarantine_Rsn_Cd` |
| Counts and processing | record counts, attempts, heartbeat, promoted time, error text |

Unique on `(bucket, key, COALESCE(version, etag))`.

**`ComplianceBatchOverride`**, reopens, waivers and carry-forwards:

| Column(s) | Notes |
|---|---|
| `Override_Ty` | `LATE_ARRIVAL_REOPEN` / `CORRECTION_REOPEN` / `SOURCE_WAIVER` / `RULE_WAIVER` / `CARRY_FORWARD` |
| `Req_ID` | Not null except for `RULE_WAIVER` |
| `Extract_ID` | Required for `RULE_WAIVER` |
| Denormalized grain | Copied from the batch or extract |
| `Btch_ID`, `Candidate_Load_ID`, `Reviewed_Load_ID` | Reviewed must equal candidate to approve (§12.4) |
| `Prior_Load_ID`, `Prior_Resolution_Ty`, `Prior_Req_Stat` | Snapshot at reopen |
| `Rule_Ref` | Rule being waived |
| `Reuse_Btch_ID` | `CARRY_FORWARD`: optional on request; set to the reused batch on approval |
| `Apprvl_Stat` | `PENDING_REVIEW` / `APPROVED` / `REJECTED` / `REVOKED` |
| Decision fields | Approver/rejecter/revoker, timestamps and reasons |
| `Promotion_Stat`, `Promoted_Dtts` | Reopens only |
| `Last_Processed_Apprvl_Stat` | Decision-processor watermark |
| `History` | Text |

Partial unique indexes enforce D-02. `REVOKED` is allowed only for waivers and carry-forwards (D-48, D-70).

**`ComplianceExtractControl`**, one row per extract grain:

| Column(s) | Notes |
|---|---|
| Grain | Project, table, run type, report period |
| `Required_Src_Cnt` | Snapshot, see below |
| `Received_Src_Cnt`, `Waived_Src_Cnt` | Counts |
| `Included_Src_Cds`, `Missing_Src_Cds`, `Waived_Src_Cds` | Source lists |
| `Carried_Src_Cds` | Included sources that are carried forward (D-70) |
| `Extract_Stat` | `PENDING` / `PARTIAL` / `COMPLETE` |
| `Extract_Rules_Stat` | `PENDING` / `PASSED` / `PASSED_WITH_WARNINGS` / `FAILED` / `ERROR` |
| `Earliest_Trigger_Dt` | max `Earliest_Close_Dt` of its batches |
| `Eligibility_Cd` | `NOT_ELIGIBLE` / `MANUAL_ONLY` / `AUTO` |
| `Eligibility_Rsn_Txt` | Reason text |
| `Trigger_Stat` | `NOT_TRIGGERED` / `REQUESTED` / `TRIGGERED` / `FAILED` |
| `Last_Trigger_ID`, `Trigger_Cnt` | Trigger tracking |
| `Retrigger_Required_Ind` | Set when data changed after the last trigger |
| `Extract_Close_Ind`, `Extract_Closed_Dtts` | Close state |
| `Combine_Run_Cnt`, `Combine_Last_Run_Dtts`, `Combine_Last_Trigger_Cd`, `Combine_Btch_ID_List` | Combine tracking |
| `Failed_Rule_Refs`, `Data_Signature`, `Triggered_Data_Signature` | Failed period rules; hash of the (Btch_ID, Load_ID) pairs at the last combine and at the last trigger |
| `Triggered_Combine_Run_Nbr` | Combine run the last trigger used; detects data changes after a trigger |

`Required_Src_Cnt` is a snapshot of the active, effective crosswalk rows for (project, table, run type) when the row is created. For ADHOC it counts the batches the intake created. ⚠ Q-05: an ad-hoc intake that adds a source later.

**`ComplianceExtractTrigger`**, one row per call attempt:

| Column(s) | Notes |
|---|---|
| `Trigger_ID` | Also passed to the job as an idempotency token when configured |
| `Extract_ID` | Extract grain |
| `Trigger_Ty` | `AUTO` / `MANUAL` / `RETRIGGER` |
| `Requested_By` | Who or what asked |
| `Warning_Txt`, `Ack_Warnings_Ind` | Warnings shown and acknowledged |
| `Rendered_Params_Txt` | Parameters sent |
| `Extract_Job_Ref` | Glue job name or endpoint called (D-72) |
| `Combine_Run_Nbr` | Combine run used |
| `Call_Stat` | `REQUESTED` / `SUCCEEDED` / `FAILED` |
| `Job_Run_Ref` | e.g. Glue `JobRunId`, HTTP request id |
| `Response_Txt`, timestamps | Call result |

At most one `REQUESTED` row per extract.

**`ComplianceRequestInTake`:**

| Column(s) | Notes |
|---|---|
| `Intake_ID` | PK |
| Project, table, run type | Required |
| `Src_Cd` | Null = all configured sources |
| `Req_Ty` | `CYCLE_INIT` / `ADHOC_REQUEST` / `CORRECTION_REQUEST` |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Required for all types, because the batch identity is the period |
| `Rsn` | Reason |
| Requester | Who asked and when |
| `Intake_Stat` | `NEW` / `PROCESSED` / `PARTIALLY_PROCESSED` / `FAILED` |
| Processed time, error text | Outcome |

`CORRECTION_REQUEST` also requires `Src_Cd`. ADHOC requires a run type in the ADHOC category, and CYCLE_INIT one in the ROUTINE category (checked by the processor).

### 5.3 Audit (append-only; written only through `audit.EventLogger`)
- **`ComplianceRequestFileDetail`:** per-batch events (`Req_ID` NOT NULL, plus `Load_ID`, `Ovrd_ID`, `Intake_ID`, `Trigger_ID`, `Event_Ty`, `Entry_Ty` AUTO/MANUAL, file ref, actor, text, time).
- **`CMS_ComplianceExceptionsAudit`:** all other audit and exception events, including files that never matched a batch. It carries optional context ids (project, table, source, run type, `Req_ID`, `Load_ID`, `Ovrd_ID`, `Extract_ID`, `Intake_ID`, `Trigger_ID`, `Btch_ID`, file ref) and `Notified_Ind`.
- **No PHI** in any free-text field or notification.

**Event catalog** (FD = FileDetail, EA = ExceptionsAudit; severity in brackets):

| Area | FD events | EA events |
|---|---|---|
| Batches | `BATCH_CREATED` [I], `BATCH_CLOSED` [I] | `INTAKE_FAILED` [E], `INTAKE_DUPLICATE_PERIOD` [W] |
| File intake | `FILE_RECEIVED` [I], `FILE_REPLACED_BEFORE_CLOSE` [I] | `FILE_REJECTED_UNPARSEABLE` [E], `FILE_REJECTED_AMBIGUOUS_TEMPLATE` [E], `FILE_REJECTED_INVALID_TOKEN` [E], `FILE_REJECTED_RUNTY_NOT_CONFIGURED` [E], `FILE_REJECTED_NO_BATCH` [E], `FILE_REJECTED_DUPLICATE` [W], `FILE_EVENT_REPLAY_IGNORED` [I], `FILE_SAME_CONTENT_OTHER_BATCH` [W], `FILE_PARSE_ERROR` [E], `FILE_COLUMN_COUNT_MISMATCH` [E], `FILE_TRAILER_COUNT_MISMATCH` [E], `FILE_ZERO_RECORDS_REJECTED` [E] |
| Validation / load | `FILE_PROMOTED` [I], `FILE_RULES_FAILED` [E] | `RULES_VALIDATION_FAILED` [E], `RULES_ENGINE_TECHNICAL_FAILURE` [E], `CORE_LOAD_ROWCOUNT_MISMATCH` [E], `INVALID_STATUS_TRANSITION` [E] |
| Reopen | `LATE_ARRIVAL_RECEIVED` [I], `CORRECTION_RECEIVED` [I], `REOPEN_PROMOTED` [I] | `REOPEN_REJECTED_RULES_FAILED` [E], `REOPEN_CANDIDATE_CREATED` [I], `REOPEN_CANDIDATE_REPLACED` [I], `LATE_ARRIVAL_REOPEN_APPROVED` [I], `CORRECTION_REOPEN_APPROVED` [I], `REOPEN_RESTAGED_FROM_ARCHIVE` [W], `REOPEN_PROMOTION_FAILED` [E] |
| Decisions | `CARRY_FORWARD_APPLIED` [I], `CARRY_FORWARD_REMOVED` [I] | `OVERRIDE_REJECTED` [I], `SOURCE_WAIVER_APPROVED` [W], `RULE_WAIVER_APPROVED` [W], `CARRY_FORWARD_APPROVED` [W], `WAIVER_REVOKED` [W] (waivers and carry-forwards), `APPROVAL_INVALID_DETECTED` [E] |
| Correction flags | `CORRECTION_FLAGGED` [W], `CORRECTION_FLAG_CLEARED` [I] | `DATA_QUALITY_ISSUE_FLAGGED` [W] |
| Extract | — | `PERIOD_RULES_FAILED` [E], `EXTRACT_ELIGIBILITY_CHANGED` [I], `EXTRACT_TRIGGER_REQUESTED` [I], `EXTRACT_TRIGGERED` [I], `EXTRACT_TRIGGERED_WITH_WARNINGS` [W], `EXTRACT_TRIGGER_BLOCKED` [W], `EXTRACT_TRIGGER_FAILED` [E], `EXTRACT_RETRIGGER_REQUIRED` [W], `SOURCE_MISSING_AT_CLOSE` [W], `BATCH_CLOSE_DEFERRED_LOCKED` [I] |
| Operations | — | `BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED` [W], `FILE_TYPE_NOT_SUPPORTED` [E], `FILE_MOVE_FAILED` [W], `EXTRACT_TRIGGER_RECONCILE_REQUIRED` [E] |
| Config | — | `CONFIG_VALIDATION_FAILED` [E] |

### 5.4 Target tables (framework columns)

| Table | Columns | Index |
|---|---|---|
| Staging (never truncated) | `Btch_ID`, `Load_ID`, `Src_File_Nm`, `Stg_Load_Dtts` | `(Btch_ID, Load_ID)` |
| Core | `Btch_ID`, `Load_ID`, `Current_Ind`, `Load_Dtts`, `End_Dtts` | `(Btch_ID) WHERE Current_Ind = 1`, `(Load_ID)`; partition by month (D-57) |

- Positional mapping (D-59) maps file column *n* to the *n*-th **business** column of the staging table in ordinal order. Business columns are all columns except the four framework columns.
- Many sources can share one staging/core table, because every operation is scoped by `Btch_ID`.

---

## 6. State Machines

### 6.1 `Req_Stat` (D-69)
The values are a CHECK constraint on CRC; the legal moves are `common.TRANSITIONS` (any other move raises `InvalidStatusTransition`).

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
| `PENDING` / `EXCEPTION_PENDING` | `CARRIED_FORWARD` | Carry-forward approved |
| `CARRIED_FORWARD` | `PENDING` | Carry-forward revoked |
| `PROMOTED` / `CARRIED_FORWARD` | `COMPLETED` | Extract triggered |
| `EXCEPTION_PENDING` | `COMPLETED_WITH_EXCEPTION` | Extract triggered (prior data or none) |
| `PENDING` | `DATA_NOT_PROVIDED` | Extract triggered |
| `COMPLETED` / `COMPLETED_WITH_EXCEPTION` / `DATA_NOT_PROVIDED` | `COMPLETED` | Reopen promoted (stays closed, D-04) |

### 6.2 Override `Apprvl_Stat`
```
REOPENS:  [*] -> PENDING_REVIEW --(newer valid file)--> PENDING_REVIEW (in place)
          PENDING_REVIEW -> APPROVED (SQL, Reviewed_Load_ID = Candidate_Load_ID) -> promotion
          APPROVED --(newer valid file, before or after promotion)--> PENDING_REVIEW (in place; type kept, D-47)
          PENDING_REVIEW -> REJECTED (SQL)
WAIVERS / CARRY_FORWARD:
          [*] -> PENDING_REVIEW (SQL insert) -> APPROVED | REJECTED (SQL)
          APPROVED -> REVOKED (SQL; only while the extract is not triggered, D-48)
          CARRY_FORWARD APPROVED -> REVOKED by SYSTEM when a file is promoted into the open batch (D-70)
```

### 6.3 Extract
- **`Extract_Stat`:** `COMPLETE` if received (own file or carried forward) + waived ≥ required; `PARTIAL` if some sources have data or are waived but not all; `PENDING` if none do.
- **`Trigger_Stat`:** `NOT_TRIGGERED` → `REQUESTED` → `TRIGGERED` | `FAILED`. A later `RETRIGGER` goes through `REQUESTED` again.
- **Closure:** `Extract_Close_Ind = 1` from the first successful trigger. It stays 1 through re-triggers.

---

## 7. Process Flows

Each flow is an idempotent service. **State changes and their audit events commit in the same transaction.**

**P1. Config change.** Apply rows (SourceSystem → RunType → Xwalk → SourceFileConfig → RuleBinding), then run `validate-config`. Job-level values (period, extract job, modes) are set on the project's scheduled jobs (D-71, D-72). Any failure → `CONFIG_VALIDATION_FAILED` and the change is not activated. Deactivation is soft (`Active_Ind`, `Effective_End_Dt`). A new compliance version end-dates the old crosswalk row and inserts a new one (D-51).

**P2. Scheduled batch creation (`create-batches --project --run-type --period [--table] [--as-of]`).** The external schedule of the project runs this command (D-71):
1. The run type must be active and ROUTINE.
2. **Run date** = `as_of` (default now) in `BUSINESS_TZ`. It is also `Req_Dt_Key` (D-29).
3. Compute the report period from the run date with the named period SQL (`period_sql.py` or `--period-file`; lookback arguments where the statement needs them).
4. Take the active crosswalk rows of the project / run type (optionally one table) that are effective on the run date. `Required_Src_Cnt` per table = the number of those rows.
5. For each row: **ensure the extract row exists first** (insert-if-absent with the `Required_Src_Cnt` snapshot and `Earliest_Trigger_Dt`); skip if that extract is already triggered (`BATCH_CREATE_SKIPPED_EXTRACT_TRIGGERED`); then `INSERT` the batch unless it exists (D-30 — a second run in the same period finds it).
6. On insert: `Earliest_Close_Dt` from the hold; `Seq` / `Btch_ID` under the table/source/run-type lock; `PENDING`, `Created_By = SCHEDULER`; raise the extract's `Earliest_Trigger_Dt` if later; log `BATCH_CREATED`.

**P3. Missed runs.** There is no catch-up job. Re-run P2 with `--as-of <missed date>`: the period follows that date and `Req_Dt_Key` is that date. (v3's cron expansion, go-live date and lookback horizon are removed.)

**P4. Intake (`process-intake`).** Lock `NEW` rows with `FOR UPDATE SKIP LOCKED`.

- **CYCLE_INIT** (D-50):
  1. The run type must be ROUTINE.
  2. Fan out to the active crosswalk rows (or the one named `Src_Cd`) effective for the period.
  3. Create batches for the intake's period with `Created_By = CYCLE_INIT` and `Intake_ID` set.
  4. Existing batches are skipped; ⚠ Q-18 whether that should count as a failure.
- **ADHOC_REQUEST:**
  1. The run type must be ADHOC.
  2. Fan out as above.
  3. If a batch already exists for any (table, source, period) → that source fails (`INTAKE_DUPLICATE_PERIOD`, D-35).
  4. The others are created.
  5. `Intake_Stat` = `PROCESSED` / `PARTIALLY_PROCESSED` / `FAILED`.
- **Zero configured sources** → `FAILED`, `INTAKE_FAILED`.
- **CORRECTION_REQUEST:**
  1. Resolve exactly one batch by (project, table, source, run type, period). None found → `FAILED`.
  2. Log `CORRECTION_FLAGGED` (FD, MANUAL) and `DATA_QUALITY_ISSUE_FLAGGED`.
  3. No other state changes.
  4. The flag clears (`CORRECTION_FLAG_CLEARED`) when a `CORRECTION_REOPEN` for that batch is promoted.
  5. No aging (D-56).

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
9. If anything was promoted → `refresh-extract` (early-completion check, D-21).
10. If the batch was `CARRIED_FORWARD`, the promoted file replaces the carry-forward: `Reuse_Btch_ID` is cleared and the override is revoked by `SYSTEM` (`CARRY_FORWARD_REMOVED`, D-70).

**P6. Promotion.** Core swap per §10.2.

**P7. Decisions (`process-decisions`).** Poll overrides where `Apprvl_Stat ≠ Last_Processed_Apprvl_Stat` (`FOR UPDATE SKIP LOCKED`). First validate each row: actor and time present, reviewed load equals candidate, legal transition, and for waiver revocation, the extract is not yet triggered. An invalid row → `APPROVAL_INVALID_DETECTED` + alert, and nothing else happens.

| Detected | Action |
|---|---|
| Reopen `APPROVED` | Under the batch lock: verify the candidate's staged rows exist. If they don't, **re-stage from the archive** by S3 version id with a SHA check (D-53, `REOPEN_RESTAGED_FROM_ARCHIVE`). Promote (§10.2). CRC: `NEW_FILE`, `Current_Load_ID`, `COMPLETED`, `Reuse_Btch_ID` cleared, still closed (D-04). Log `REOPEN_PROMOTED`, `*_REOPEN_APPROVED`, and `CORRECTION_FLAG_CLEARED` if flagged. `Promotion_Stat = PROMOTED`. Then **refresh the extract and auto re-trigger** (D-41, §11.4). On failure: `Promotion_Stat = FAILED`, `REOPEN_PROMOTION_FAILED`, retry next poll. |
| Reopen `REJECTED` | Log `OVERRIDE_REJECTED`; the candidate load becomes `SUPERSEDED`; CRC unchanged. |
| Waiver `APPROVED` / `REJECTED` / `REVOKED` | Log the event, then `refresh-extract` (the eligibility may change). |
| Carry-forward `APPROVED` | Valid only if the run type has `Carry_Fwd_Ind = 1`, the batch is open with no promoted data, the extract is not triggered, and an earlier closed batch with data exists (the requested `Reuse_Btch_ID`, else the latest by report start; a carried batch resolves to its source). CRC: `CARRY_FORWARD`, `Reuse_Btch_ID`, `CARRIED_FORWARD`. Log `CARRY_FORWARD_APPROVED` and `CARRY_FORWARD_APPLIED`, then refresh the extract (D-70). |
| Carry-forward `REVOKED` / `REJECTED` | Revoked: CRC back to `PENDING` (`CARRY_FORWARD_REMOVED`); then refresh the extract. |

Finally set `Last_Processed_Apprvl_Stat`.

**P8. Waivers (D-48).**
- **`SOURCE_WAIVER`:** for an open batch with no usable data or in exception.
- **`RULE_WAIVER`:** for an extract whose latest period run failed that rule.
- Both are inserted and approved through the Appendix B SQL.
- They only affect STRICT eligibility (§11.2) and never touch batch or core data.

**P9. Refresh (`refresh-extract`).** Under the extract lock:
1. **Recount.** Received = batches with `NEW_FILE` or `CARRY_FORWARD`; carried sources are also listed in `Carried_Src_Cds`. Waived = batches without data that have an approved `SOURCE_WAIVER`. Update `Extract_Stat` and the source lists.
2. **Decide whether to combine.** Combine runs if the trigger is early completion and the period is complete, or if the trigger is SLA evaluation, a manual trigger request, a reopen promotion, a waiver decision or a manual refresh.
3. **Combine** (§10.4), then run the **period-level rules** with mode `PERIOD_RULES_MODE` (D-63). Set `Extract_Rules_Stat`, and log the contributing `Btch_ID`s for each failed rule (`PERIOD_RULES_FAILED`).
4. **Update** `Combine_*`.
5. **Compute** eligibility (§11.2).
   - If it changed → `EXTRACT_ELIGIBILITY_CHANGED`.
   - If data changed since the last trigger → `Retrigger_Required_Ind = 1`, `EXTRACT_RETRIGGER_REQUIRED`.

**P10. Evaluate and auto-trigger (`evaluate-extracts [--project] [--table] [--run-type] [--as-of]`).** For each extract in the job's scope that is not triggered, or needs a re-trigger, with `as_of ≥ Earliest_Trigger_Dt` in `BUSINESS_TZ`: refresh (P9); if `Eligibility_Cd = AUTO` → trigger (§11.3, type `AUTO` or `RETRIGGER`) with the job's extract settings (D-72). A sweep with work to do and no extract job configured fails with `ConfigError`.

**P11. Manual trigger (`trigger-extract`).** Refresh first, then apply the §11.2 rules:
- Not eligible → `EXTRACT_TRIGGER_BLOCKED`, exit non-zero.
- `MANUAL_ONLY` with warnings → requires `--ack-warnings`, then trigger and log `EXTRACT_TRIGGERED_WITH_WARNINGS`.

**P12. Close.** This is part of the trigger (§11.3). There is no separate close job.

**P13. Notifications.**
- Triggered by `ComplianceEventType.Notify_Ind`.
- Channel comes from the event type, overridable by the file-config row (SES, SNS or both, D-54); the SNS topic is `SNS_TOPIC_ARN`.
- Recipients come from the file config.
- Set `Notified_Ind` after sending. A failed notification never rolls back pipeline state.

---

## 8. File-Resolution Decision Tables

These apply after C0–C14 pass, with the batch lock held. A batch with an applied carry-forward counts as "has current data" (so O-2 / O-4 apply), and a closed carried batch reopens as `LATE_ARRIVAL_REOPEN` (D-70). **PASS** = file-level rules passed (with or without warnings) and the zero-record rule is satisfied. **FAIL** = GATE failure or a disallowed zero-record file.

### 8.1 Batch OPEN (`Batch_Close_Ind = 0`)

| # | Prior promoted load? | Result | CRC | Core | Load_Stat | Events |
|---|---|---|---|---|---|---|
| O-1 | no | PASS | `NEW_FILE`, `Current_Load_ID` = this, `PROMOTED` | swap | `PROMOTED` | `FILE_RECEIVED`, `FILE_PROMOTED` |
| O-2 | yes | PASS | `NEW_FILE`, `Current_Load_ID` → this, `PROMOTED` (latest arrival wins even if `{TS}` is older, D-37; a carry-forward is replaced, D-70) | swap (prior rows disabled) | this `PROMOTED`, prior `SUPERSEDED` | + `FILE_REPLACED_BEFORE_CLOSE` |
| O-3 | no | FAIL | `Resolution` NULL, `EXCEPTION_PENDING` | none | `RULES_FAILED` | `FILE_RULES_FAILED`, `RULES_VALIDATION_FAILED` |
| O-4 | yes | FAIL | keeps `NEW_FILE` on the prior load (or the carry-forward), `EXCEPTION_PENDING` (D-46) | none | `RULES_FAILED` | same as O-3 |
| O-5 | any | technical error | unchanged | rolled back | `FAILED_TECHNICAL` | `RULES_ENGINE_TECHNICAL_FAILURE`; retry |

### 8.2 Batch CLOSED (reopen)

| # | Active reopen override for this batch | Result | Action | Type | Load_Stat | Events |
|---|---|---|---|---|---|---|
| X-1 | none (or only `REJECTED`) | PASS | **Insert** `PENDING_REVIEW`: candidate = this, `Prior_*` snapshot | `Resolution = MISSING` or `CARRY_FORWARD` → `LATE_ARRIVAL_REOPEN`; `NEW_FILE` → `CORRECTION_REOPEN` | `PENDING_APPROVAL` | `REOPEN_CANDIDATE_CREATED` + `LATE_ARRIVAL_RECEIVED` / `CORRECTION_RECEIVED` |
| X-2 | `PENDING_REVIEW` | PASS | **Update in place** (candidate → this, reviewed = NULL, History) | unchanged | this `PENDING_APPROVAL`, previous `SUPERSEDED` | `REOPEN_CANDIDATE_REPLACED` |
| X-3 | `APPROVED`, not yet promoted | PASS | **Update in place** → `PENDING_REVIEW`; the old approval no longer applies | unchanged | previous `SUPERSEDED` | `REOPEN_CANDIDATE_REPLACED` |
| X-4 | `APPROVED`, promoted | PASS | **Update in place** → `PENDING_REVIEW`, `Prior_*` refreshed, `Promotion_Stat` reset | **kept** (D-47) | `PENDING_APPROVAL` | `REOPEN_CANDIDATE_REPLACED` |
| X-5 | any | FAIL | **No override change** (D-45) | — | `RULES_FAILED` | `FILE_RULES_FAILED`, `REOPEN_REJECTED_RULES_FAILED` |

Staging the reopen file first deletes staged rows for that `Btch_ID` (D-05). Core is untouched until approval.

---

## 9. Filename Templates & Matching (D-28 – D-37)

### 9.1 Template grammar

A template is literal text plus exactly one of each placeholder:

| Placeholder | Matches | Validation |
|---|---|---|
| `{PROJECT}` | the row's `Project_Alias`, literally | equality |
| `{TABLE}` | the row's `Table_Alias`, literally | equality |
| `{SRC}` | the row's `Src_Alias`, literally | equality |
| `{RUNTY}` | `[A-Za-z0-9]+` (⚠ Q-03 character set) | must equal a `Run_Ty` exactly (D-31) **and** have an active, effective crosswalk row for (project, table, source, run type) (D-33) |
| `{RPTSTART}`, `{RPTEND}` | `\d{8}` | valid `YYYYMMDD` dates (D-32), end ≥ start |
| `{TS}` | `\d{14}` | valid `YYYYMMDDHHMMSS` (D-36); used only for uniqueness (D-37) |

- The extension is part of the literal text, e.g. `…_{TS}.csv`.
- The template is anchored to the whole object name (the basename after the configured inbound prefix).
- Literal text is regex-escaped.
- **Adjacent placeholders must be separated by at least one literal character.** This keeps parsing unambiguous and is enforced by the validator.
- Case sensitivity: ⚠ Q-03.

**Generic example** (illustrative only): template `{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt`, with aliases `PRJA` / `TBLX` / `SRC1`, matches `PRJA_TBLX_SRC1_MONTHLY_20260101_20260131_20260201093000.txt`.

### 9.2 Matching algorithm
1. Compile every active config's template into a regex (cached; rebuilt on config change).
2. Match the object name against all of them. It must match **exactly one** config.
3. Validate the tokens (§9.1) and resolve `Run_Ty` and its crosswalk row.
4. Look up the batch by the full grain: config's (project, table, source) + `Run_Ty` + (report start, report end).

**Effective-date check for the crosswalk row** (D-51): the row must be effective for the file's report period (⚠ Q-04: which date is used).

### 9.3 Non-overlap (config time)
Two active configs **conflict** if their templates could match the same name. The validator checks this in two ways:
- **Rule-based:** any two configs whose templates differ only in placeholder positions, and whose alias triples produce equal literal skeletons, conflict.
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
  FROM <stg_schema>.<Stg_Tblnm> WHERE Btch_ID = :btch_id AND Load_ID = :load_id;  -- appended_cnt
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
`Btch_ID` (which period and source) + `Load_ID` (which physical file) + `CRC.Current_Load_ID` (which load is current) + `ComplianceFileLoad` (S3 version, SHA).

### 10.4 Combine
```sql
SELECT c.*
  FROM ComplianceRequestControl r
  JOIN <core_schema>.<Table_Nm> c ON c.Btch_ID = r.Btch_ID AND c.Current_Ind = 1
 WHERE r.Project_Cd = :p AND r.Table_Nm = :t AND r.Run_Ty = :rt
   AND r.Rpt_Start_Dt_Key = :s AND r.Rpt_End_Dt_Key = :e
   AND r.Resolution_Ty = 'NEW_FILE'
   AND c.Load_ID = r.Current_Load_ID
UNION ALL                                   -- carried sources read the reused batch (D-70)
SELECT c.*
  FROM ComplianceRequestControl r
  JOIN ComplianceRequestControl src ON src.Btch_ID = r.Reuse_Btch_ID
  JOIN <core_schema>.<Table_Nm> c ON c.Btch_ID = src.Btch_ID AND c.Current_Ind = 1 AND c.Load_ID = src.Current_Load_ID
 WHERE <same grain> AND r.Resolution_Ty = 'CARRY_FORWARD';
```
- Period-level GRE rules receive `{btch_id_list}` and read core directly (same DB, D-43).
- The same Btch_ID list (reused batch ids included) is available to the extract job as `{btch_id_list}` in `EXTRACT_PARAMS`.

---

## 11. Extract Trigger & Batch Close (D-38 – D-42, D-48, D-49)

### 11.1 Hold
- **Per batch:** `Earliest_Close_Dt = Req_Dt_Key + (SLA_Days − 1)`.
- **Per extract:** `Earliest_Trigger_Dt` = the maximum over its batches.
- **Rule:** before `Earliest_Trigger_Dt` begins (in `BUSINESS_TZ`), **no trigger of any kind is allowed.** The hold is absolute; waivers don't bypass it.

### 11.2 Eligibility (computed on every refresh)

`complete = Received + Waived ≥ Required` (Received includes carried-forward sources, D-70). The mode is `EXTRACT_GATING_MODE` (D-72). `rules_ok = Extract_Rules_Stat IN (PASSED, PASSED_WITH_WARNINGS)`, or `FAILED` where every failed GATE rule has an approved `RULE_WAIVER`.

| Mode | `AUTO` when | `MANUAL_ONLY` when | `NOT_ELIGIBLE` when |
|---|---|---|---|
| any | Hold passed **and** `Received = Required` **and** `Extract_Rules_Stat IN (PASSED, PASSED_WITH_WARNINGS)`, i.e. fully complete with no waivers needed (D-40) | — | Hold not passed |
| `STRICT_ALL_PASS` | (above) | Hold passed **and** `complete` **and** `rules_ok`, where at least one waiver was needed (⚠ Q-06: should that also be AUTO?) | Otherwise: missing sources without a waiver, or failed rules without a waiver |
| `BEST_EFFORT` | (above) | Hold passed, and anything else: partial, failed rules or zero data. Warnings are listed and must be acknowledged (D-40, D-49) | Hold not passed |

- `Extract_Rules_Stat = ERROR` (technical failure) → `NOT_ELIGIBLE` until a refresh succeeds.
- `Extract_Rules_Stat = PENDING` → `NOT_ELIGIBLE` (combine has not run).

### 11.3 Trigger and close (the only way a batch closes, D-39)

Under the extract lock, plus a **try-lock on every batch in the grain**. If any batch is busy → `BATCH_CLOSE_DEFERRED_LOCKED`; try again on the next sweep, or exit non-zero for a manual trigger.

1. Insert `ComplianceExtractTrigger` (`REQUESTED`). Set `Trigger_Stat = REQUESTED`. Log `EXTRACT_TRIGGER_REQUESTED`. **Commit.**
2. Render the parameters from `EXTRACT_PARAMS` (D-72): literal text with placeholders for extract attributes, `{btch_id_list}`, `{load_id_list}` and `{trigger_id}`. The job called is stored in `Extract_Job_Ref`.
3. **Call** the job:
   - Glue: `start_job_run` returning a `JobRunId`.
   - HTTP: a 2xx response, with auth ⚠ Q-08.
   - The framework does **not** wait for or check the job's result (D-39).
4. **Call accepted**, in one transaction:
   - Trigger → `SUCCEEDED` (store `Job_Run_Ref`).
   - Extract: `Trigger_Stat = TRIGGERED`, `Extract_Close_Ind = 1`, `Triggered_Combine_Run_Nbr`, `Retrigger_Required_Ind = 0`, `Trigger_Cnt + 1`.
   - **Every open batch in the grain:**
     - unresolved → `Resolution = MISSING`, `DATA_NOT_PROVIDED` (+ `SOURCE_MISSING_AT_CLOSE`);
     - `NEW_FILE` or `CARRY_FORWARD` → `COMPLETED`;
     - in exception → `COMPLETED_WITH_EXCEPTION`.
     - All get `Batch_Close_Ind = 1` and `Closed_By_Trigger_ID`, and log `BATCH_CLOSED`.
   - Log `EXTRACT_TRIGGERED` (or `…_WITH_WARNINGS`).
5. **Call rejected or errored:** trigger → `FAILED`, `Trigger_Stat = FAILED`, `EXTRACT_TRIGGER_FAILED`, alert. Batches **stay open**. Retry policy is ⚠ Q-07.
6. **Crash between steps 3 and 4:** the trigger row stays `REQUESTED`. The next sweep reconciles it:
   - If `Job_Run_Ref` was stored, or the job can be queried by `Trigger_ID`, complete step 4.
   - Otherwise alert for manual resolution. Blind re-calls are not allowed, because they risk a duplicate extract (⚠ Q-07 idempotency).

### 11.4 Re-trigger after reopen (D-41)
1. Reopen promotion → P9 refresh → `Retrigger_Required_Ind = 1`.
2. If eligibility is `AUTO` → trigger with type `RETRIGGER` (immediately when the `process-decisions` job has the extract job configured, otherwise on the project's next `evaluate-extracts` run). The batches are already closed, so step 4 only updates the extract and trigger rows (and the reopened batch's status).
3. If eligibility is `MANUAL_ONLY` or `NOT_ELIGIBLE` → `EXTRACT_RETRIGGER_REQUIRED` alert, and a person decides (⚠ Q-09).

⚠ **Q-16:** automatic re-trigger means a corrected extract is regenerated, and possibly resubmitted, without a human step. The downstream job must handle that.

### 11.5 Late files after close
The reopen path (§8.2). A new source cannot join a triggered extract through a file, because batches are created only by the scheduler or an intake (D-15).

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
| Decisions | `Last_Processed_Apprvl_Stat` + `SKIP LOCKED` |
| Intake | `Intake_Stat = NEW` + `SKIP LOCKED` |
| Extract trigger | One `REQUESTED` row per extract (partial unique index); `Trigger_ID` passed downstream |

### 12.2 Locks
Session advisory locks (`pg_try_advisory_lock` / `pg_advisory_lock` with a timeout), keyed by hashed strings:

| Key | Used by | Why |
|---|---|---|
| `EXT:<Extract_ID>` | refresh, trigger | One refresh or trigger per extract at a time |
| `BTCH:<Btch_ID>` | ingest, promotion, decisions, trigger | One writer per batch |
| `SEQ:<Project>|<Table>|<Src>|<Run_Ty>|<Req_Dt_Key>` | batch creation | Computing `Seq` |

- **Lock order is always EXT → BTCH.** Ingest takes only BTCH. When ingest needs a refresh, it releases BTCH first.
- Locks are released automatically if a process dies.
- A `Heartbeat_Dtts` older than N minutes with no lock held means a crash → alert.
- **Direct DB connections only** (D-55). RDS Proxy / PgBouncer transaction pooling would silently break session locks.
- **Locks live in the framework database** (D-65).

### 12.3 Invariants enforced in the database
- Unique grains.
- Override partial unique indexes.
- A single `REQUESTED` trigger per extract.
- Non-overlapping crosswalk effective windows (GiST exclusion; needs `btree_gist`, ⚠ Q-11).
- All CHECKs in `schema.sql` (Appendix A), including the fixed `Req_Stat` list (D-69).
- FKs to the lookup tables (source system, run type, event type).

### 12.4 Manual-approval race
**Race:** a reviewer approves candidate load *n* while a newer file *n+1* has already replaced it.

**Guard:** the Appendix B templates update `WHERE Candidate_Load_ID = :reviewed AND Apprvl_Stat = 'PENDING_REVIEW'` and set `Reviewed_Load_ID`. A stale approval updates 0 rows. A DB CHECK (`Reviewed_Load_ID = Candidate_Load_ID` when `APPROVED`) blocks hand-written SQL that skips the template.

---

## 13. Edge Cases & Negative Scenarios

✔ = handled by a decision or rule in this doc. ⚠ = depends on an open question.

| # | Scenario | Handling | St |
|---|---|---|---|
| E-01 | Name matches no template | C2 quarantine | ✔ |
| E-02 | Name matches more than one template | Prevented by the validator; C3 at runtime | ✔ |
| E-03 | Aliases match but the run type isn't configured or effective for that source | C5 | ✔ |
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
| E-26 | Crash after staging, before swap | Heartbeat alert; replay restarts the same load (C0) | ✔ |
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
| E-38 | ADHOC intake adds a new source to a period whose extract already exists or was triggered | ⚠ Q-05 | ⚠ |
| E-39 | CYCLE_INIT for a period that already has batches | Skipped | ⚠ Q-18 |
| E-40 | Correction request matches no batch | `INTAKE_FAILED` | ✔ |
| E-41 | Correction flag never cleared | No aging (D-56) | ✔ |
| E-42 | Late file for a `MISSING` batch | X-1 `LATE_ARRIVAL_REOPEN` | ✔ |
| E-43 | Corrected file for a `NEW_FILE` batch | X-1 `CORRECTION_REOPEN` | ✔ |
| E-44 | Newer file while a reopen is pending | X-2 | ✔ |
| E-45 | Newer file after approval, before promotion | X-3 | ✔ |
| E-46 | Second correction after promotion | X-4, type kept (D-47) | ✔ |
| E-47 | Reopen file fails GATE | X-5 (D-45) | ✔ |
| E-48 | Approved candidate's staged rows are gone | Re-stage from archive (D-53) | ✔ |
| E-49 | Archive object also missing or SHA differs | `REOPEN_PROMOTION_FAILED` + alert | ✔ |
| E-50 | Stale approval | §12.4 | ✔ |
| E-51 | Hand-written invalid approval | DB CHECK / `APPROVAL_INVALID_DETECTED` | ✔ |
| E-52 | Waiver revoked after the extract was triggered | Processor rejects it (`APPROVAL_INVALID_DETECTED`) | ✔ |
| E-53 | Waiver approved, then the real data arrives before the trigger | Received counts first; the waiver no longer matters | ✔ |
| E-54 | Trigger attempted during the hold | Blocked (§11.1) | ✔ |
| E-55 | STRICT: a source never arrives | Waiver → `MANUAL_ONLY` (Q-06) | ✔ |
| E-56 | BEST_EFFORT: zero data | Manual trigger with warning (D-49) | ✔ |
| E-57 | Period rules fail | STRICT blocked unless rule waivers; BEST_EFFORT manual with warning | ✔ |
| E-58 | Period rules technical error | `NOT_ELIGIBLE` until a successful refresh | ✔ |
| E-59 | A file is mid-load when a trigger fires | Try-lock; trigger deferred | ✔ |
| E-60 | Extract API rejects the call | Batches stay open; retry | ⚠ Q-07 |
| E-61 | API accepted the call but the framework crashed before closing | Reconcile via `Job_Run_Ref` / `Trigger_ID`; no blind re-call | ⚠ Q-07 |
| E-62 | Extract job itself fails downstream | Out of scope (D-39); batches remain closed | ✔ (accepted) |
| E-63 | Reopen promoted after close | Auto re-trigger if AUTO; otherwise alert | ⚠ Q-09 |
| E-64 | Auto re-trigger causes resubmission downstream | Downstream responsibility | ⚠ Q-16 |
| E-65 | Replacement file arrives while the batch is open and after an early refresh | Re-promote; `Retrigger_Required` is not set, since nothing was triggered yet | ✔ |
| E-66 | Extract never triggered (no data, nobody runs the manual trigger) | Health query lists it | ⚠ Q-17 |
| E-67 | Illegal status move | `InvalidStatusTransition` (D-69) | ✔ |
| E-68 | Carry-forward requested for a run type with `Carry_Fwd_Ind = 0`, a batch with data, a closed batch, or with no earlier batch with data | `APPROVAL_INVALID_DETECTED`; batch unchanged (D-70) | ✔ |
| E-69 | File arrives for an open carried-forward batch | O-2: the file wins; the carry-forward is revoked by `SYSTEM` | ✔ |
| E-70 | File arrives after a carried-forward batch closed | X-1 `LATE_ARRIVAL_REOPEN` | ✔ |
| E-71 | Waiver and carry-forward requested for the same batch | Partial unique index (D-02) | ✔ |
| E-72 | Reused batch is corrected later | Its new current load changes the extract's data signature → re-trigger required (§11.4) | ✔ |
| E-73 | Scheduled extract job has no `EXTRACT_JOB_NAME` / endpoint, or `EXTRACT_PARAMS` names an unknown placeholder | `ConfigError`, exit 2, nothing is called | ✔ |
| E-68 | Connection pooler in the path | Not allowed (D-55) | ✔ |
| E-69 | Two config rows produce an alias collision after an edit | Validator blocks the change | ✔ |
| E-70 | Bursts of files beyond job concurrency | Orchestration queue (Phase 2) | ✔ (D-13) |

---

## 14. Feasibility, Drawbacks & Risks

**Feasible as designed:**
- The control plane on RDS Postgres (low volume).
- The in-database core swap.
- Template-regex matching.
- Period-based batch identity.
- Removing carry-forward, which removes the most complex logic from v2.

| Risk / drawback | Impact | Mitigation |
|---|---|---|
| Positional mapping without header-name checks (D-59) | A reordered file with the same column count loads silently into the wrong columns | GRE content rules (type or format checks per column) as the first GATE rules |
| Batches close only on trigger (D-39) | A period nobody triggers stays open indefinitely; late files keep replacing data without approval | Health query and alert (Q-17) |
| The framework doesn't learn the extract job's outcome (D-39) | Batches are closed even if the extract failed downstream | Downstream monitoring; `Trigger_ID` for correlation |
| Auto re-trigger after corrections (D-41) | Possible automatic resubmission | Q-16 |
| Lost response on the trigger call | Risk of a duplicate extract if blindly retried | Reconciliation step; `Trigger_ID` idempotency (Q-07) |
| xlsx / large files read in memory (pandas) | Memory limits | `LOAD_ENGINE=SPARK` for that job (D-62); file types Q-02 |
| Spark staging speed | Slow JDBC writes | Bounded partitions, batchsize |
| Session advisory locks | Need direct connections | D-55 |
| Superseded core rows kept forever (D-57) | Storage growth | Monthly partitions; index on current rows |
| New extract connector type | Needs a package deploy | Connector types `GLUE_JOB` and `HTTP_API` from day one; new periods need only a project period file (D-71) |
| Manual SQL approvals (D-12) | No maker/checker | `framework_approver` role (D-64), guarded templates, processor validation |
| `btree_gist` needed for the effective-date exclusion | Extension must be allowed on RDS | Q-11; fall back to validator-only enforcement |
| One database for control and data (D-65) | Staging/core volume shares the database with the control tables | Monthly partitions (D-57); separate schemas and roles |
| Job-level settings (D-67, D-71, D-72) | Two jobs of one project could run with different gating / rule modes; settings are not visible in the database | Keep each project's settings in one job definition (Terraform); `show-config` prints values and sources; each trigger stores the called job and rendered parameters |
| Carry-forward (D-70) | Reused data may be stale for the new period | Manual approval per batch, only for run types that allow it; visible in `Carried_Src_Cds` and the audit trail |
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
├── batches.py    create-batches (P2), intake (P4), CRC / extract rows
├── ingest.py     file pipeline (P5, P6), decision tables (§8)
├── load.py       file reading, staging engines, core swap (§10.2), archive re-stage
├── overrides.py  decisions, waivers, carry-forward (P7, P8, D-70)
├── extract.py    eligibility (§11.2), refresh/combine (P9), trigger + close (P11, P12), sweep (P10)
├── audit.py      audit writer, notifications (P13)
├── adapters.py   S3 / local store, GRE, Glue / HTTP connectors, SES / SNS
└── sql/          schema.sql  seed.sql  approvals.sql
```

**Key contracts** (services take the connection, the clock and the settings):
- `TemplateMatcher.match(object_name) -> MatchResult` (raises `MatchError` with the quarantine code)
- `ingest.decide(ResolutionInput) -> ResolutionDecision` (a pure function)
- `load.swap(conn, cfg, btch_id, load_id, expected_rows, now) -> PromotionResult`
- `RuleEngine.run(conn, bindings, run_params, mode) -> RuleOutcome`
- `extract.compute_eligibility(EligibilityInput, strict_waiver_auto) -> Eligibility` (a pure function)
- `ExtractTriggerService.fire(extract_id, trigger_ty, requested_by, ack_warnings) -> TriggerOutcome`
- `ExtractConnector.call(settings, rendered_params) -> CallResult(accepted, job_run_ref, response_txt, ambiguous)`
- `batches.compute_period(conn, name, run_date, lookback_days, lookback_weeks, period_file) -> (start, end)`

### 15.2 Tests
- **Unit:**
  - Template compile/match (every §9.1 rule, including separator and ambiguity rules, and generated-name overlap tests).
  - `resolution_engine` (every §8 row).
  - Eligibility (every §11.2 cell).
  - Hold arithmetic (SLA 1 / 2 / N, timezone boundaries).
  - `Btch_ID` / `Seq`, period SQL (month ends, leap day, quarter and year boundaries), the status guard, settings precedence and parameter rendering.
- **Integration** (PostgreSQL with `schema.sql`; Docker optional): one named test per ✔ row in §13; the Appendix C walkthrough replayed with `--as-of`; a negative test for every constraint.
- **Engine parity:** pandas vs Spark on the same fixtures.
- **Concurrency:** ingest vs trigger, two ingests on one batch, decisions vs ingest, and a crash/replay of a trigger.
- **Connector:** fake Glue and HTTP servers covering accept, reject, timeout and lost response.
- **Coverage:** every §8 row, every carry-forward path (D-70); ≥ 90% line coverage of the package.

### 15.3 Operations
- **Alarms:** job-level failure alarms per entry point (Phase 2).
- **Health queries:**
  - stale heartbeats;
  - pending reviews older than N hours;
  - failed promotions;
  - `REQUESTED` triggers older than N minutes;
  - extracts past `Earliest_Trigger_Dt` and not triggered (Q-17);
  - `Retrigger_Required_Ind = 1`;
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
| ~~Q-01~~ | *Answered for now by D-69 (fixed list). If the business list differs, change the CHECK constraint and `common.TRANSITIONS`.* | `common.py`, `schema.sql` | — |
| **Q-02** | Supported file types (`.txt` / `.csv` / `.xlsx` / `.parquet`?). Delimiter, quote and escape characters; encoding; for xlsx, sheet name and header row; trailer record layout and whether its count must equal the data rows. | **`load.py` (file reading)** | csv/txt only in the first release |
| **Q-03** | Filename matching: case-sensitive or not? Allowed characters in `{RUNTY}`? Any other placeholders? Are files only at the inbound prefix root, or also in sub-folders? | **`templates`** | Case-sensitive; `[A-Za-z0-9]`; root only |
| **Q-04** | Which date decides whether a crosswalk row (and its compliance version) is effective: for files, the report start or end date; for scheduled batches, the run date? | `ingest`, `batches` | File: `Rpt_Start_Dt_Key` (`FILE_EFFECTIVE_DATE_BASIS`); scheduled: run date |
| **Q-05** | An ADHOC intake adds a new source to a period whose extract row already exists or was already triggered. Allow it (raise `Required_Src_Cnt`, mark re-trigger required) or reject it? | intake | Allow before the trigger; reject after |
| **Q-06** | STRICT: when completeness or rules are satisfied only through approved waivers, is the trigger automatic or manual? | `control` | Manual (`MANUAL_ONLY`) |
| **Q-07** | Extract call: retry count and backoff on failure. Can the job/API accept a `Trigger_ID` for idempotency? Can the framework query a call's status after a crash? | **`trigger`** | 3 retries, exponential backoff; `TRIGGER_ID` param |
| **Q-08** | HTTP API authentication (IAM SigV4, API key in Secrets Manager, OAuth client credentials), timeout, and which response codes count as "accepted". | `adapters.HttpApiConnector` | — |
| **Q-09** | After a reopen, if the extract is no longer AUTO-eligible (e.g. STRICT rules now fail), is an alert-only response right? | `evaluator` | Alert only |
| **Q-10** | Zero-byte file when no header is expected: treat it as a zero-record file (D-60 applies)? | `ingest` | Yes |
| **Q-11** | RDS PostgreSQL version, and is the `btree_gist` extension allowed? | DDL | ≥ 14 with `btree_gist` |
| **Q-12** | GRE call mechanics: import it as a Python library or call a GRE entry point? Naming for rule group and variant per (project, table, source, scope)? Which GRE tables or columns give pass/fail per rule and the completion marker? | **`adapters` (rules engine)** | Library call; group = `project.table`, variant = `src|scope` |
| **Q-13** | Phase-2 orchestration choice (D-13). | Phase 2 only | — |
| **Q-14** | Migration: order of existing processes, parallel-run period and go-live date per project (the first scheduled run defines it). | Rollout | — |
| **Q-15** | Data classification (PHI) per source, KMS keys, and who holds `framework_approver`. | Security | — |
| **Q-16** | Auto re-trigger after a correction may regenerate or resubmit an extract already sent externally. Is that acceptable, or should post-submission re-triggers always be manual? | `evaluator` | Manual after the first successful trigger (would amend D-41) |
| **Q-17** | Should extracts past `Earliest_Trigger_Dt` and still not triggered raise an alert, and after how many days? | ops | Alert after 1 day |
| **Q-18** | CYCLE_INIT for a period whose batches already exist: skip silently (`PROCESSED`) or fail? | intake | Skip, logged |
| ~~Q-19~~ | *Withdrawn in v4: one database (D-65); GRE receives one connection.* | — | — |

---

## 17. Change Log

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

## Appendix B: Approval / Waiver / Carry-forward SQL Templates (D-12, D-48, D-64, D-70)

The templates are maintained in [`src/framework/sql/approvals.sql`](../../src/framework/sql/approvals.sql). Run them as `framework_approver`, with `search_path` set to the metadata schema (D-65). **Each must report 1 row.** A result of 0 means the candidate changed or the row was already decided; re-review.

| Template | Guard |
|---|---|
| Approve a reopen | `PENDING_REVIEW` and `Candidate_Load_ID = :reviewed_load_id` (§12.4) |
| Reject a pending reopen, waiver or carry-forward | `PENDING_REVIEW` |
| Request a `SOURCE_WAIVER` | batch open |
| Request a `RULE_WAIVER` | extract not triggered |
| Request a `CARRY_FORWARD` (optional `:reuse_btch_id`) | batch open and without a current load; the run type must allow it (checked on approval) |
| Approve a waiver or carry-forward | `PENDING_REVIEW` |
| Revoke an approved waiver or carry-forward | extract not triggered |

---

## Appendix C: Synthetic Walkthrough

All names are placeholders.

**Config**
- Project `PRJA`, table `tbl_x`, run type `MONTHLY` (`SLA_Days = 2`, `Carry_Fwd_Ind = 1`).
- Scheduled jobs: `create-batches --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH` at 06:00 on the 1st; `evaluate-extracts --project PRJA` every 15 minutes with `EXTRACT_JOB_NAME=extract_prja_tbl_x`, `EXTRACT_GATING_MODE=STRICT_ALL_PASS`.
- Sources `S1` and `S2`, both with template `{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt`.
- Aliases: `PRJA` / `TBLX` / `S1` and `PRJA` / `TBLX` / `S2`.

| When | Event | Result |
|---|---|---|
| Feb 1 06:00 | `create-batches` runs; period Jan 1–31 | Two batches: `20260201_PRJA_tbl_x_S1_MONTHLY_<v>_1` and `…_S2_…_1`. `Earliest_Close_Dt` = Feb 2. Extract row created with `Required = 2`. |
| Feb 1 09:30 | `PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt` | Matched; promoted (O-1). Extract `PARTIAL`. |
| Feb 1 11:00 | `PRJA_TBLX_S2_MONTHLY_20260101_20260131_20260201105500.txt` | Promoted. Complete → early combine → period rules `PASSED`. Eligibility `NOT_ELIGIBLE` (hold until Feb 2). |
| Feb 1 12:00 | S1 resend with new `{TS}` and new content | O-2 replacement; early combine re-runs. |
| Feb 2 00:15 | `evaluate-extracts` | Hold passed; complete; rules passed → `AUTO` → Glue job called and accepted → both batches closed (`COMPLETED`), extract `TRIGGERED`. |
| Feb 5 | Corrected S2 file | Batch is closed → X-1 `CORRECTION_REOPEN` `PENDING_REVIEW`. |
| Feb 5 | Approver runs the template | P7 promotes; batch stays closed; refresh → `AUTO` → `RETRIGGER` (subject to Q-16). |
| Feb 6 | `PRJA_TBLX_S3_MONTHLY_…` (unknown alias) | C2 `FILE_REJECTED_UNPARSEABLE`. |
| Feb 6 | `PRJA_TBLX_S1_MONTHLY_20260201_20260228_…` (February batch not created yet) | C6 `FILE_REJECTED_NO_BATCH`. |
| Mar 1 06:00 | `create-batches`; period Feb 1–28 | February batches for S1 and S2. |
| Mar 1 | S1 February file | Promoted. S2 has nothing. |
| Mar 1 | Analyst requests and approves `CARRY_FORWARD` for S2 | `process-decisions`: S2 February → `CARRIED_FORWARD`, `Reuse_Btch_ID` = S2 January batch. Extract `COMPLETE`, `Carried_Src_Cds = S2`; combine reads S1 February + S2 January rows. |
| Mar 2 00:15 | `evaluate-extracts` | `AUTO` → triggered; `--BATCHES` lists S1 February and S2 January; S2 February closes `COMPLETED` / `CARRY_FORWARD`. |
| Mar 3 | S2 February file arrives | X-1 `LATE_ARRIVAL_REOPEN`; after approval it is promoted, `Reuse_Btch_ID` is cleared and the extract is re-triggered. |
