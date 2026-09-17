# CMS Compliance Framework: Consolidated Design (v3)

| | |
|---|---|
| **Status** | Build-ready for every module that §16 (Open Questions) does not name as blocked. |
| **Revision** | v3.1, 2026-09-16 (implementation notes added: Appendix A columns, extra operational events; code in `src/framework`). v3, 2026-09-16. Replaces v2 and v1. v3 is **project-agnostic** and **filename-driven**. Carry-forward is removed, batches are keyed by report period, and batches close only when the framework triggers the extract. See §17. |
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
17. Change Log (v2 → v3)
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

**Adding a project, table, source or run type is config only** (D-27). The package has no project-specific code paths, names or branches. Two things still need a package deploy:
- A new period strategy (`.sql` file).
- A new extract-job *type* (the connector), as opposed to a new job name, which is config.

**In scope:** config and validation; batch creation (scheduled, catch-up, on-demand cycle, ad-hoc); file intake (template match, dedupe, quarantine); staging; file-level validation (UMcM GRE); core promotion; reopen approvals; waivers; combine and period-level validation; extract eligibility; **calling** the extract job/API; batch close; audit and notifications.

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
| D-02 | **Override keys.** At most one active reopen override per batch (`Req_ID`). At most one active `SOURCE_WAIVER` per batch. At most one active `RULE_WAIVER` per (extract, rule). |
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
| D-29 | **`Req_Dt_Key`** is the **actual date the batch was created**. It is embedded in `Btch_ID` and is **not** in filenames. A batch created late by catch-up still uses the actual creation date. |
| D-29b | **Late batches.** A batch created late computes its report period from the **scheduled** date it should have been created on. |
| D-30 | **One batch per period.** Exactly **one batch per (Project, Table, Source, Run type, Report start, Report end)**, including ADHOC. This is the CRC grain. |
| D-31 | **Filename tokens.** `{PROJECT}`, `{TABLE}` and `{SRC}` must equal **aliases stored on the file-config row**. `{RUNTY}` must equal a `Run_Ty` code exactly. |
| D-32 | **Date format.** Filename dates are `YYYYMMDD`. |
| D-33 | **One file-config row per (Project, Table, Source).** `{RUNTY}` selects the run type, which must be configured for that source. |
| D-34 | **Carry-forward is removed entirely.** A batch without usable data at close is `MISSING`. |
| D-35 | **Duplicate ad-hoc intake.** A second ADHOC intake for a (table, source, period) that already has a batch is rejected (`Intake_Stat = FAILED`). |
| D-36 | **`{TS}`.** Filenames carry a unique token `{TS}` = `YYYYMMDDHHMMSS`. |
| D-37 | **Latest arrival wins.** `{TS}` only makes names unique and is not used for ordering. |
| D-38 | **SLA hold.** `SLA_Days` is a **minimum hold** before a batch may close. A batch may close from the start of `Req_Dt_Key + (SLA_Days − 1)` calendar days: SLA 1 = the creation day, SLA 2 = any time the next day, and so on. |
| D-39 | **Closing a batch.** Batches close **only** when the framework successfully **calls the configured extract job/API** (with configured parameters). The status is then set to request complete. Extract generation itself, and its outcome, are outside the framework. |
| D-40 | **Triggering the extract.** Triggering is **automatic** when the period is fully eligible (all sources have data, period rules passed, SLA hold over). It is **manual** (command) otherwise. **BEST_EFFORT:** manual trigger allowed any time after the SLA hold, with warnings. **STRICT_ALL_PASS:** allowed only when all sources and rules succeed, **except** through manual approval (waivers). |
| D-41 | **Re-trigger after reopen.** After a closed batch's reopen is promoted, combine and rules re-run and the extract is **re-triggered automatically** under the same eligibility rules. |
| D-42 | **Extract job config.** The extract job/API and its **parameters are configurable** per (project, table, run type), including extra job-specific parameters. |
| D-43 | **GRE location.** GRE metadata and results live in the **same Postgres database** as the framework. |
| D-44 | **GATE/ANNOTATE** is set **per (project, table, source)**. |
| D-45 | **Reopen file fails GATE.** The file is rejected; no override is created; an alert is raised. |
| D-46 | **Replacement fails GATE while open.** The prior promoted data stays current; the batch goes to `EXCEPTION_PENDING`. |
| D-47 | **Second correction.** A second correction on a batch whose reopen was `LATE_ARRIVAL_REOPEN` keeps that type (update in place). |
| D-48 | **Waiver mechanics.** Waivers are a manual SQL insert followed by a manual SQL approval. `RULE_WAIVER` is per rule. A waiver can be revoked until the extract is triggered. |
| D-49 | **BEST_EFFORT with no data.** No minimum-data requirement; a zero-data trigger is allowed with a warning. |
| D-50 | **`CYCLE_INIT`.** An intake with `Req_Ty = CYCLE_INIT` creates the routine batches for a named period on demand. |
| D-51 | **`Cmplnc_Vrsn`.** An attribute on **effective-dated** crosswalk rows. A new version end-dates the old row and adds a new one. |
| D-52 | **Same content, different batch.** Content identical to another batch's file is allowed and logged as a warning. |
| D-53 | **Missing staged rows.** If an approved reopen's staged rows are gone at promotion, the file is **re-staged from the S3 archive** (checksum verified). |
| D-54 | **Notifications.** Sent via SES and/or SNS; the channel is configurable per event type and per file-config row. |
| D-55 | **Database.** RDS PostgreSQL (version ⚠ Q-11), direct connections (no RDS Proxy). |
| D-56 | **Correction flags do not age.** No aging alert. |
| D-57 | **Retention.** Keep everything; partition large tables by month. |
| D-58 | **File wrappers.** Plain files only: no compression, encryption or control files. |
| D-59 | **Column mapping.** By **position**. The data column count must equal the staging business-column count, otherwise `FILE_PARSE_ERROR`. Header names are **not** checked. |
| D-60 | **Zero-record files.** Allowed or rejected per file-config row (`Allow_Zero_Rcd_Ind`). |
| D-61 | **Columns not loaded.** Identity and generated core columns are skipped automatically, plus an optional per-config exclude list. |
| D-62 | **Engine.** File volumes are mixed; the engine (pandas / Spark) is chosen per file-config row. |
| D-63 | **Period-level validation mode.** `Period_Rules_Vld_Md` on `ComplianceExtractPolicy`. *(You had no preference; this is the design choice.)* |
| D-64 | **Approver role.** Approvals run under a dedicated `framework_approver` DB role limited to the override table. *(You had no preference; this is the design choice.)* |

Also carried from v1: gating modes `STRICT_ALL_PASS` / `BEST_EFFORT` per (project, table, run type); extract generation is external; notification recipient lists are comma-delimited text; the local package is built first; `Req_Stat` is enforced by a lookup table (**the list is pending from you, Q-01**).

### 2.2 Withdrawn
All of these were withdrawn by D-34, D-30 or D-39:
- **Carry-forward (D-34):** D-06 (revoke → candidate), D-18 (`Used_Btch_ID` = anchor), D-22 (carry-forward vs reopen), D-23 (carry-forward counts as received), D-24 (anchor expiry).
- **Grain change (D-30):** D-10 (`Intake_ID` in the grain).
- **Close change (D-39 / D-38):** D-16 (SLA closes batches).
- **Minimum data (D-49):** D-20 (BEST_EFFORT needs ≥1 source).
- **Filename rules (D-28–D-37):** D-07 (filename tokens, now superseded).

---

## 3. Architecture & Build Phasing

**One package, thin callers.** The `framework/` package holds all logic and has no AWS-orchestration imports. Entry points only parse arguments, call one service and set the exit code. Every time-based command takes `--as-of`; nothing inside calls "today" directly.

**Phase 1 (build now, local: pytest + Postgres in Docker + Spark local mode):**

| CLI command | Service | Eventual trigger (D-13) |
|---|---|---|
| `validate-config` | `config.validator.validate_all` | CI, and before any config change is applied |
| `create-batches --as-of` | `batches.scheduler.run` | cron |
| `catchup --as-of` | `batches.catchup.run` | cron (hourly) |
| `process-intake` | `batches.intake_processor.run` (CYCLE_INIT, ADHOC, CORRECTION) | poll |
| `ingest-file --bucket --key [--version-id]` | `ingest.pipeline.process_file` | S3 event |
| `process-decisions` | `overrides.decision_processor.run` | poll (every few minutes) |
| `evaluate-extracts --as-of` | `extract.evaluator.run` (refresh + auto-trigger sweep) | cron (every 15 min) |
| `trigger-extract --extract-id --requested-by [--ack-warnings]` | `extract.trigger.manual` | human |
| `refresh-extract --extract-id` | `extract.control.refresh` | chained / human |

**Phase 2 (deferred):** wrap the same services in Glue or Step Functions. Constraints for Phase 2:
- The file pipeline needs a dedicated DB session for its whole run, because it holds session advisory locks (§12).
- Approvals are detected by polling.
- S3 bursts must be queued beyond the job concurrency limit.

---

## 4. Core Concepts & Glossary

| Term | Definition |
|---|---|
| **Batch (CRC row)** | One per `(Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)` (D-30). Current-state; updated in place; never duplicated. |
| **Report period** | `Rpt_Start_Dt_Key..Rpt_End_Dt_Key`. Scheduled batches get it from the period strategy, computed from the *scheduled* date (D-29b). CYCLE_INIT and ADHOC batches get it from the intake. Files carry it in the name. |
| **`Req_Dt_Key`** | The actual batch creation date (D-29), used in `Btch_ID` and in the SLA hold. |
| **`Btch_ID`** | `{Req_Dt_Key:YYYYMMDD}_{Project_Cd}_{Table_Nm}_{Src_Cd}_{Run_Ty}_{Cmplnc_Vrsn}_{Seq}`. `Seq` = 1 + the number of batches already created on that `Req_Dt_Key` for the same (project, table, source, run type), computed under lock. Seq is needed because catch-up or CYCLE_INIT can create several periods on one day. Unique; never changes. |
| **`Load_ID`** | One physical file (`ComplianceFileLoad`). Separates the original file, replacements and corrections within the same `Btch_ID`. |
| **Resolution** | `NEW_FILE` (the batch has a promoted load), `MISSING` (closed with no usable data), or `NULL` (open, no usable data yet). |
| **SLA hold** | `Earliest_Close_Dt = Req_Dt_Key + (SLA_Days − 1)`. The batch cannot close before the start of that calendar day in `Business_Tz` (D-38). |
| **Extract grain** | `(Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)`. All sources' batches for the same period roll up into one row. |
| **Eligibility** | Whether an extract may be triggered, and whether that happens automatically or manually (§11). |
| **Trigger** | A framework call to the configured extract job/API. When the call is accepted, every batch in the grain closes (D-39). |
| **Reopen** | A valid file for a closed batch. It needs approval before promotion (D-04). |
| **Waiver** | An approved override that treats a missing or failed source (`SOURCE_WAIVER`) or a failed period rule (`RULE_WAIVER`) as satisfied for STRICT eligibility. |

---

## 5. Data Model

Full DDL is in Appendix A.
- **Schema:** `cms_compliance`. PascalCase names are unquoted, so Postgres folds them to lowercase.
- **Types:** timestamps are `TIMESTAMPTZ` (UTC). Indicators are `SMALLINT` 0/1.
- **Audit columns:** every config table carries `Created_*` / `Updated_*`.

### 5.1 Configuration

| Table | Key | Purpose / rules |
|---|---|---|
| `ComplianceSourceSystem` | `Src_Cd` | Source master. |
| `ComplianceRunType` | `Run_Ty` | `Run_Category_Cd` (`ROUTINE` / `ADHOC`). `SLA_Days ≥ 1` (hold, D-38). |
| `CompliancePeriodStrategy` | `Period_Strategy_Cd` | Strategy code → `.sql` file + required parameters. |
| `ComplianceDataSetSourceXwalk` | `(Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt)` | Which sources feed which table under which run type. Holds `Cmplnc_Vrsn`, period strategy + parameters, `Schedule_Cron_Expr` (ROUTINE only), `Business_Tz`, `Active_Ind`, effective window. **Effective windows for the same 4-part key cannot overlap** (GiST exclusion constraint, D-51). |
| `ComplianceSourceFileConfig` | `Cfg_ID`; one active row per `(Project_Cd, Table_Nm, Src_Cd)` (D-33) | The file contract (below). |
| `ComplianceExtractPolicy` | `(Project_Cd, Table_Nm, Run_Ty)` | `Extract_Gating_Md` (default `STRICT_ALL_PASS`), `Period_Rules_Vld_Md` (D-63), extract job connector (`Extract_Job_Ty` = `GLUE_JOB` / `HTTP_API`, job name or endpoint, auth secret), retry settings (⚠ Q-07). **Required** for every (project, table, run type) in the crosswalk, because the job config lives here. |
| `ComplianceExtractJobParam` | `(Project_Cd, Table_Nm, Run_Ty, Param_Nm)` | Configurable parameters (D-42). `Param_Src_Cd` = `LITERAL` / `EXTRACT_ATTR` / `BTCH_ID_LIST` / `LOAD_ID_LIST` / `TRIGGER_ID`. `Param_Val` holds the literal, or the attribute name for `EXTRACT_ATTR`. |
| `ComplianceRuleBinding` | `(Project_Cd, Table_Nm, Src_Cd, Rule_Scope_Cd, Gre_Rule_Group, Gre_Rule_Variant)` | Links a scope to GRE rules. `Src_Cd = '*'` means all sources, used for `PERIOD_LEVEL`. The GATE/ANNOTATE mode does **not** live here: `FILE_LEVEL` uses the file-config mode (D-44), `PERIOD_LEVEL` uses the policy mode (D-63). ⚠ Q-12 (GRE call details). |
| `ComplianceRequestStatus` / `ComplianceRequestStatusTransition` | status / (from, to, trigger) | **Values pending Q-01.** The transition table enforces legal moves. |
| `ComplianceEventType` | `Event_Ty` | Event vocabulary: log table, category, severity, `Notify_Ind`, `Notify_Channel_Cd` (D-54). |

**`ComplianceSourceFileConfig` columns**

| Group | Columns |
|---|---|
| Identity | `Project_Cd`, `Table_Nm`, `Src_Cd` |
| Filename | `Src_File_Nm_Tmplt` (D-28), `Project_Alias`, `Table_Alias`, `Src_Alias` (D-31) |
| File format | `Src_File_Ty`, `Delmtr_Cd`, `Line_Term_Cd`, `Src_File_Has_Hdr_Ind`, `Src_File_Has_Trlr_Ind` (⚠ Q-02) |
| Handling | `Allow_Zero_Rcd_Ind` (D-60), `Engine_Cd` (D-62), `Rules_Vld_Md` (D-44), `Is_Rules_Engine_Required`, `Load_Exclude_Col_List` (D-61) |
| S3 paths | inbound, archive, quarantine |
| Targets | staging schema and table; core schema and table (`Core_Tblnm = Table_Nm`, D-25) |
| Notifications | business and delivery-owner groups, success/failure recipient lists, subject/body text, `Notify_Channel_Cd`, `Sns_Topic_Arn` |

**`config.validator` rejects:**
- Templates missing any required placeholder, using a placeholder twice, or using an unknown placeholder (§9.1).
- Two active configs that could match the same filename (§9.3).
- A file config with no active crosswalk row, or an active crosswalk row with no file config.
- A `ROUTINE` crosswalk row without a cron, or an `ADHOC` row with one.
- Strategy parameters missing for the chosen strategy (also enforced by DB CHECK).
- A crosswalk (project, table, run type) with no `ComplianceExtractPolicy` row.
- An extract job parameter referring to an unknown attribute.
- Crosswalk rows for the same (project, table, run type) whose strategies give different periods for the same scheduled date. All sources of an extract must share its period.
- An invalid cron expression or timezone.
- `Core_Tblnm ≠ Table_Nm`.

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
| `Req_Stat` | FK to the status lookup (Q-01) |
| `Resolution_Ty` | `NEW_FILE` / `MISSING` / NULL |
| `Current_Load_ID` | The load whose rows are current in core |
| `Batch_Close_Ind`, `Closed_By_Trigger_ID` | Close state (D-39) |
| `Created_By` | `SCHEDULER` / `CATCHUP` / `CYCLE_INIT` / `ADHOC_INTAKE` |
| `Created_Dtts`, `Updated_Dtts` | Audit timestamps |

CHECK constraints:
- `NEW_FILE` requires `Current_Load_ID`.
- `MISSING` requires no current load.
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

**`ComplianceBatchOverride`**, reopens and waivers only:

| Column(s) | Notes |
|---|---|
| `Override_Ty` | `LATE_ARRIVAL_REOPEN` / `CORRECTION_REOPEN` / `SOURCE_WAIVER` / `RULE_WAIVER` |
| `Req_ID` | Not null except for `RULE_WAIVER` |
| `Extract_ID` | Required for `RULE_WAIVER` |
| Denormalized grain | Copied from the batch or extract |
| `Btch_ID`, `Candidate_Load_ID`, `Reviewed_Load_ID` | Reviewed must equal candidate to approve (§12.4) |
| `Prior_Load_ID`, `Prior_Resolution_Ty`, `Prior_Req_Stat` | Snapshot at reopen |
| `Rule_Ref` | Rule being waived |
| `Apprvl_Stat` | `PENDING_REVIEW` / `APPROVED` / `REJECTED` / `REVOKED` |
| Decision fields | Approver/rejecter/revoker, timestamps and reasons |
| `Promotion_Stat`, `Promoted_Dtts` | Reopens only |
| `Last_Processed_Apprvl_Stat` | Decision-processor watermark |
| `History` | Text |

Partial unique indexes enforce D-02. `REVOKED` is allowed only for waivers (D-48).

**`ComplianceExtractControl`**, one row per extract grain:

| Column(s) | Notes |
|---|---|
| Grain | Project, table, run type, report period |
| `Required_Src_Cnt` | Snapshot, see below |
| `Received_Src_Cnt`, `Waived_Src_Cnt` | Counts |
| `Included_Src_Cds`, `Missing_Src_Cds`, `Waived_Src_Cds` | Source lists |
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

### 5.3 Audit (append-only; written only through `audit/event_logger.py`)
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
| Decisions | — | `OVERRIDE_REJECTED` [I], `SOURCE_WAIVER_APPROVED` [W], `RULE_WAIVER_APPROVED` [W], `WAIVER_REVOKED` [W], `APPROVAL_INVALID_DETECTED` [E] |
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

### 6.1 `Req_Stat`: ⚠ values pending Q-01
Until your list arrives, the logic is written against these **abstract states**. Each will map to one of your values.

| Abstract state | Meaning |
|---|---|
| `S_AWAITING` | Open, no usable data |
| `S_VALIDATED` | Transient: staged and passed, promotion in progress |
| `S_PROMOTED` | Open, current data promoted |
| `S_EXCEPTION` | Open; the latest file failed GATE (prior data may still be current, D-46) |
| `S_COMPLETE` | Closed with data (request complete, D-39) |
| `S_COMPLETE_EXCEPTION` | Closed while in `S_EXCEPTION` |
| `S_NOT_PROVIDED` | Closed with no data (`MISSING`) |

| From | To | Trigger |
|---|---|---|
| — | `S_AWAITING` | Batch created |
| `S_AWAITING` / `S_PROMOTED` / `S_EXCEPTION` | `S_VALIDATED` | File passed file-level rules |
| `S_VALIDATED` | `S_PROMOTED` | Core swap committed |
| `S_AWAITING` / `S_PROMOTED` / `S_EXCEPTION` | `S_EXCEPTION` | File failed GATE |
| `S_PROMOTED` | `S_COMPLETE` | Extract triggered |
| `S_EXCEPTION` | `S_COMPLETE_EXCEPTION` | Extract triggered (prior data or none) |
| `S_AWAITING` | `S_NOT_PROVIDED` | Extract triggered |
| `S_COMPLETE` / `S_COMPLETE_EXCEPTION` / `S_NOT_PROVIDED` | `S_COMPLETE` | Reopen promoted (stays closed, D-04) |

`ComplianceRequestStatusTransition` holds the real values; `common/status.py` rejects any other move (`INVALID_STATUS_TRANSITION`).

### 6.2 Override `Apprvl_Stat`
```
REOPENS:  [*] -> PENDING_REVIEW --(newer valid file)--> PENDING_REVIEW (in place)
          PENDING_REVIEW -> APPROVED (SQL, Reviewed_Load_ID = Candidate_Load_ID) -> promotion
          APPROVED --(newer valid file, before or after promotion)--> PENDING_REVIEW (in place; type kept, D-47)
          PENDING_REVIEW -> REJECTED (SQL)
WAIVERS:  [*] -> PENDING_REVIEW (SQL insert) -> APPROVED | REJECTED (SQL)
          APPROVED -> REVOKED (SQL; only while the extract is not triggered, D-48)
```

### 6.3 Extract
- **`Extract_Stat`:** `COMPLETE` if received + waived ≥ required; `PARTIAL` if some sources have data or are waived but not all; `PENDING` if none do.
- **`Trigger_Stat`:** `NOT_TRIGGERED` → `REQUESTED` → `TRIGGERED` | `FAILED`. A later `RETRIGGER` goes through `REQUESTED` again.
- **Closure:** `Extract_Close_Ind = 1` from the first successful trigger. It stays 1 through re-triggers.

---

## 7. Process Flows

Each flow is an idempotent service. **State changes and their audit events commit in the same transaction.**

**P1. Config change.** Apply rows (SourceSystem → RunType → PeriodStrategy → Xwalk → SourceFileConfig → ExtractPolicy → JobParam → RuleBinding), then run `validate-config`. Any failure → `CONFIG_VALIDATION_FAILED` and the change is not activated. Deactivation is soft (`Active_Ind`, `Effective_End_Dt`). A new compliance version end-dates the old crosswalk row and inserts a new one (D-51).

**P2. Scheduled batch creation (`create-batches --as-of`).** For each active ROUTINE crosswalk row, for each cron fire time in `(last_run, as_of]` (in `Business_Tz`):
1. Let **scheduled date** = the fire date. It must be inside the row's effective window.
2. Compute the report period from the scheduled date (strategy SQL).
3. **Ensure the extract row exists first** (insert-if-absent with the `Required_Src_Cnt` snapshot and `Earliest_Trigger_Dt`). The CRC row references it (`Extract_ID NOT NULL`).
4. `INSERT … ON CONFLICT (grain) DO NOTHING`.
   - If a daily cron covers a longer period, only the first fire creates the batch (D-30).
5. On insert:
   - `Req_Dt_Key` = actual date of `as_of` (D-29).
   - `Earliest_Close_Dt` from the hold.
   - `Seq` / `Btch_ID` (under the table/source/run-type lock).
   - Status `S_AWAITING`, `Created_By = SCHEDULER`.
   - Raise the extract's `Earliest_Trigger_Dt` to this batch's `Earliest_Close_Dt` if later.
   - Log `BATCH_CREATED`.

**P3. Catch-up (`catchup --as-of`).**
1. Expand each ROUTINE cron over `[max(Effective_Start_Dt, go-live, as_of − lookback), as_of]` (⚠ Q-14 for go-live and lookback).
2. For every scheduled date whose period batch is missing, run P2 with `Created_By = CATCHUP`.
3. The period comes from the scheduled date (D-29b); `Req_Dt_Key` is the actual date.

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
5. **File-level rules** (if `Is_Rules_Engine_Required`): GRE with `{btch_id, load_id}`.
   - GATE failure (mode from the file config, D-44) → whole-file failure (D-19).
   - ANNOTATE-only failures → passed with warnings.
   - A technical failure → `FAILED_TECHNICAL`, raise and retry; CRC unchanged.
6. **Resolve** per §8.
7. **Archive** the S3 object after commit. A failure here is retried when the event replays (C0).
8. **Release** locks.
9. If anything was promoted → `refresh-extract` (early-completion check, D-21).

**P6. Promotion.** Core swap per §10.2.

**P7. Decisions (`process-decisions`).** Poll overrides where `Apprvl_Stat ≠ Last_Processed_Apprvl_Stat` (`FOR UPDATE SKIP LOCKED`). First validate each row: actor and time present, reviewed load equals candidate, legal transition, and for waiver revocation, the extract is not yet triggered. An invalid row → `APPROVAL_INVALID_DETECTED` + alert, and nothing else happens.

| Detected | Action |
|---|---|
| Reopen `APPROVED` | Under the batch lock: verify the candidate's staged rows exist. If they don't, **re-stage from the archive** by S3 version id with a SHA check (D-53, `REOPEN_RESTAGED_FROM_ARCHIVE`). Promote (§10.2). CRC: `NEW_FILE`, `Current_Load_ID`, `S_COMPLETE`, still closed (D-04). Log `REOPEN_PROMOTED`, `*_REOPEN_APPROVED`, and `CORRECTION_FLAG_CLEARED` if flagged. `Promotion_Stat = PROMOTED`. Then **refresh the extract and auto re-trigger** (D-41, §11.4). On failure: `Promotion_Stat = FAILED`, `REOPEN_PROMOTION_FAILED`, retry next poll. |
| Reopen `REJECTED` | Log `OVERRIDE_REJECTED`; the candidate load becomes `SUPERSEDED`; CRC unchanged. |
| Waiver `APPROVED` / `REJECTED` / `REVOKED` | Log the event, then `refresh-extract` (the eligibility may change). |

Finally set `Last_Processed_Apprvl_Stat`.

**P8. Waivers (D-48).**
- **`SOURCE_WAIVER`:** for an open batch with no usable data or in exception.
- **`RULE_WAIVER`:** for an extract whose latest period run failed that rule.
- Both are inserted and approved through the Appendix B SQL.
- They only affect STRICT eligibility (§11.2) and never touch batch or core data.

**P9. Refresh (`refresh-extract`).** Under the extract lock:
1. **Recount.** Received = batches with `NEW_FILE`. Waived = batches without data that have an approved `SOURCE_WAIVER`. Update `Extract_Stat` and the source lists.
2. **Decide whether to combine.** Combine runs if the trigger is early completion and the period is complete, or if the trigger is SLA evaluation, a manual trigger request, a reopen promotion, a waiver decision or a manual refresh.
3. **Combine** (§10.4), then run the **period-level rules** with mode `Period_Rules_Vld_Md` (D-63). Set `Extract_Rules_Stat`, and log the contributing `Btch_ID`s for each failed rule (`PERIOD_RULES_FAILED`).
4. **Update** `Combine_*`.
5. **Compute** eligibility (§11.2).
   - If it changed → `EXTRACT_ELIGIBILITY_CHANGED`.
   - If data changed since the last trigger → `Retrigger_Required_Ind = 1`, `EXTRACT_RETRIGGER_REQUIRED`.

**P10. Evaluate and auto-trigger (`evaluate-extracts --as-of`).** For each extract that is not triggered, or needs a re-trigger, with `as_of ≥ Earliest_Trigger_Dt` in `Business_Tz`: refresh (P9); if `Eligibility_Cd = AUTO` → trigger (§11.3, type `AUTO` or `RETRIGGER`).

**P11. Manual trigger (`trigger-extract`).** Refresh first, then apply the §11.2 rules:
- Not eligible → `EXTRACT_TRIGGER_BLOCKED`, exit non-zero.
- `MANUAL_ONLY` with warnings → requires `--ack-warnings`, then trigger and log `EXTRACT_TRIGGERED_WITH_WARNINGS`.

**P12. Close.** This is part of the trigger (§11.3). There is no separate close job.

**P13. Notifications.**
- Triggered by `ComplianceEventType.Notify_Ind`.
- Channel comes from the event type, overridable by the file-config row (SES, SNS or both, D-54).
- Recipients come from the file config.
- Set `Notified_Ind` after sending. A failed notification never rolls back pipeline state.

---

## 8. File-Resolution Decision Tables

These apply after C0–C14 pass, with the batch lock held. **PASS** = file-level rules passed (with or without warnings) and the zero-record rule is satisfied. **FAIL** = GATE failure or a disallowed zero-record file.

### 8.1 Batch OPEN (`Batch_Close_Ind = 0`)

| # | Prior promoted load? | Result | CRC | Core | Load_Stat | Events |
|---|---|---|---|---|---|---|
| O-1 | no | PASS | `NEW_FILE`, `Current_Load_ID` = this, `S_PROMOTED` | swap | `PROMOTED` | `FILE_RECEIVED`, `FILE_PROMOTED` |
| O-2 | yes | PASS | `Current_Load_ID` → this, `S_PROMOTED` (latest arrival wins even if `{TS}` is older, D-37) | swap (prior rows disabled) | this `PROMOTED`, prior `SUPERSEDED` | + `FILE_REPLACED_BEFORE_CLOSE` |
| O-3 | no | FAIL | `Resolution` NULL, `S_EXCEPTION` | none | `RULES_FAILED` | `FILE_RULES_FAILED`, `RULES_VALIDATION_FAILED` |
| O-4 | yes | FAIL | stays `NEW_FILE` on the prior load, `S_EXCEPTION` (D-46) | none | `RULES_FAILED` | same as O-3 |
| O-5 | any | technical error | unchanged | rolled back | `FAILED_TECHNICAL` | `RULES_ENGINE_TECHNICAL_FAILURE`; retry |

### 8.2 Batch CLOSED (reopen)

| # | Active reopen override for this batch | Result | Action | Type | Load_Stat | Events |
|---|---|---|---|---|---|---|
| X-1 | none (or only `REJECTED`) | PASS | **Insert** `PENDING_REVIEW`: candidate = this, `Prior_*` snapshot | `Resolution = MISSING` → `LATE_ARRIVAL_REOPEN`; `NEW_FILE` → `CORRECTION_REOPEN` | `PENDING_APPROVAL` | `REOPEN_CANDIDATE_CREATED` + `LATE_ARRIVAL_RECEIVED` / `CORRECTION_RECEIVED` |
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
- **Engine per config.**
  - pandas: `COPY` in one transaction, including the preceding `DELETE … WHERE Btch_ID`.
  - Spark: delete, then JDBC append with bounded partitions and `batchsize`.
- **Safe re-runs:** a re-run repeats the delete and reload. Promotion always filters by `Load_ID` and checks row counts, so a partial Spark load can never be promoted.
- **Column mapping:** positional.
  - With a header row, the header is skipped and names are ignored.
  - With a trailer, the trailer is removed before counting.
  - All values are read as text and cast to the staging column types; a cast failure → `FILE_PARSE_ERROR`.

### 10.2 Promotion (D-01): one Postgres transaction
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
- **`<cols>`** = staging business columns ∩ core columns, minus core identity/generated columns (from `information_schema`), minus `Load_Exclude_Col_List` (D-61).
- **Identifiers** are quoted with `psycopg.sql.Identifier`. No `SELECT *`.
- **Spark is never used for this step.** Spark JDBC can't make the disable and the append atomic.
- **Out-of-order files are safe,** because each swap touches only its own `Btch_ID`.

### 10.3 Lineage
`Btch_ID` (which period and source) + `Load_ID` (which physical file) + `CRC.Current_Load_ID` (which load is current) + `ComplianceFileLoad` (S3 version, SHA).

### 10.4 Combine
```sql
SELECT c.*
  FROM ComplianceRequestControl r
  JOIN <core_schema>.<Table_Nm> c ON c.Btch_ID = r.Btch_ID AND c.Current_Ind = 1
 WHERE r.Project_Cd = :p AND r.Table_Nm = :t AND r.Run_Ty = :rt
   AND r.Rpt_Start_Dt_Key = :s AND r.Rpt_End_Dt_Key = :e
   AND r.Resolution_Ty = 'NEW_FILE';
```
- Period-level GRE rules receive `{btch_id_list}` and read core directly (same DB, D-43).
- The same Btch_ID list is available to the extract job as a parameter.

---

## 11. Extract Trigger & Batch Close (D-38 – D-42, D-48, D-49)

### 11.1 Hold
- **Per batch:** `Earliest_Close_Dt = Req_Dt_Key + (SLA_Days − 1)`.
- **Per extract:** `Earliest_Trigger_Dt` = the maximum over its batches.
- **Rule:** before `Earliest_Trigger_Dt` begins (in `Business_Tz`), **no trigger of any kind is allowed.** The hold is absolute; waivers don't bypass it.

### 11.2 Eligibility (computed on every refresh)

`complete = Received + Waived ≥ Required`. `rules_ok = Extract_Rules_Stat IN (PASSED, PASSED_WITH_WARNINGS)`, or `FAILED` where every failed GATE rule has an approved `RULE_WAIVER`.

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
2. Render the parameters from `ComplianceExtractJobParam`: literals, extract attributes, the Btch_ID / Load_ID lists, and `TRIGGER_ID`.
3. **Call** the job:
   - Glue: `start_job_run` returning a `JobRunId`.
   - HTTP: a 2xx response, with auth ⚠ Q-08.
   - The framework does **not** wait for or check the job's result (D-39).
4. **Call accepted**, in one transaction:
   - Trigger → `SUCCEEDED` (store `Job_Run_Ref`).
   - Extract: `Trigger_Stat = TRIGGERED`, `Extract_Close_Ind = 1`, `Triggered_Combine_Run_Nbr`, `Retrigger_Required_Ind = 0`, `Trigger_Cnt + 1`.
   - **Every open batch in the grain:**
     - unresolved → `Resolution = MISSING`, `S_NOT_PROVIDED` (+ `SOURCE_MISSING_AT_CLOSE`);
     - `NEW_FILE` → `S_COMPLETE`;
     - in exception → `S_COMPLETE_EXCEPTION`.
     - All get `Batch_Close_Ind = 1` and `Closed_By_Trigger_ID`, and log `BATCH_CLOSED`.
   - Log `EXTRACT_TRIGGERED` (or `…_WITH_WARNINGS`).
5. **Call rejected or errored:** trigger → `FAILED`, `Trigger_Stat = FAILED`, `EXTRACT_TRIGGER_FAILED`, alert. Batches **stay open**. Retry policy is ⚠ Q-07.
6. **Crash between steps 3 and 4:** the trigger row stays `REQUESTED`. The next sweep reconciles it:
   - If `Job_Run_Ref` was stored, or the job can be queried by `Trigger_ID`, complete step 4.
   - Otherwise alert for manual resolution. Blind re-calls are not allowed, because they risk a duplicate extract (⚠ Q-07 idempotency).

### 11.4 Re-trigger after reopen (D-41)
1. Reopen promotion → P9 refresh → `Retrigger_Required_Ind = 1`.
2. If eligibility is `AUTO` → trigger with type `RETRIGGER`. The batches are already closed, so step 4 only updates the extract and trigger rows (and the reopened batch's status).
3. If eligibility is `MANUAL_ONLY` or `NOT_ELIGIBLE` → `EXTRACT_RETRIGGER_REQUIRED` alert, and a person decides (⚠ Q-09).

⚠ **Q-16:** automatic re-trigger means a corrected extract is regenerated, and possibly resubmitted, without a human step. The downstream job must handle that.

### 11.5 Late files after close
The reopen path (§8.2). A new source cannot join a triggered extract through a file, because batches are created only by the scheduler or an intake (D-15).

---

## 12. Idempotency & Concurrency

### 12.1 Idempotency keys
| Operation | Key / mechanism |
|---|---|
| Batch creation (all paths) | CRC grain; `INSERT … ON CONFLICT DO NOTHING` |
| Extract row | Extract grain; same |
| S3 event | `(bucket, key, version / etag)` (C0) |
| Duplicate content | SHA-256 vs current/pending load of the same batch (C11) |
| Staging | Delete + reload by `Btch_ID` |
| Promotion | `Load_Stat = PROMOTED` short-circuit |
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

### 12.3 Invariants enforced in the database
- Unique grains.
- Override partial unique indexes.
- A single `REQUESTED` trigger per extract.
- Non-overlapping crosswalk effective windows (GiST exclusion; needs `btree_gist`, ⚠ Q-11).
- All CHECKs in Appendix A.
- FKs to the lookup tables.

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
| E-29 | Scheduler double-fire, catch-up overlap, or a daily cron inside a longer period | `ON CONFLICT DO NOTHING` | ✔ |
| E-30 | Catch-up creates several missed periods on one day | `Seq` keeps `Btch_ID` unique | ✔ |
| E-31 | Batch created late (catch-up) | Period from the scheduled date; `Req_Dt_Key` actual; hold counts from the actual date | ✔ |
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
| E-67 | Illegal status move | `INVALID_STATUS_TRANSITION` | ✔ (Q-01) |
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
| xlsx / large files read in memory (pandas) | Memory limits | `Engine_Cd = SPARK` per config (D-62); file types Q-02 |
| Spark staging speed | Slow JDBC writes | Bounded partitions, batchsize |
| Session advisory locks | Need direct connections | D-55 |
| Superseded core rows kept forever (D-57) | Storage growth | Monthly partitions; index on current rows |
| New period strategy or extract connector type | Needs a package deploy, not config | Keep a broad strategy set; connector types `GLUE_JOB` and `HTTP_API` from day one |
| Manual SQL approvals (D-12) | No maker/checker | `framework_approver` role (D-64), guarded templates, processor validation |
| `btree_gist` needed for the effective-date exclusion | Extension must be allowed on RDS | Q-11; fall back to validator-only enforcement |
| Two engines (pandas / Spark) | Cast differences between them | Parity tests on the same fixtures |

---

## 15. Package Structure, Tests & Operations

### 15.1 Package
```
framework/
├── cli.py
├── config/      connection.py  settings.py  repository.py  validator.py  templates.py (compile/match §9)
├── common/      clock.py  btch_id.py  locks.py  status.py
├── audit/       event_logger.py
├── batches/     period_strategies.py (+ sql/period_strategies/*.sql)  scheduler.py  catchup.py  intake_processor.py  crc_repository.py
├── ingest/      pipeline.py  filename_matcher.py  file_reader.py  dedupe.py  resolution_engine.py
├── load/        engine/{base,pandas_engine,spark_engine}.py  staging_loader.py  promoter.py  archive_restager.py
├── overrides/   override_manager.py  decision_processor.py
├── validation/  gre_adapter.py
├── extract/     control.py (refresh, eligibility)  combiner.py  evaluator.py  trigger.py  connectors/{glue_job,http_api}.py
└── notify/      notifier.py (ses, sns)
```

**Key contracts** (all take an explicit `conn` and `clock`):
- `templates.match(object_name) -> MatchResult | MatchError`
- `resolution_engine.decide(ResolutionInput) -> ResolutionDecision` (a pure function)
- `promoter.promote(conn, btch_id, load_id) -> PromotionResult`
- `gre_adapter.run(scope, params) -> RuleOutcome`
- `control.compute_eligibility(ExtractSnapshot, Policy, as_of) -> Eligibility`
- `trigger.fire(conn, extract_id, trigger_ty, requested_by, ack) -> TriggerResult`
- `connectors.*.call(rendered_params) -> CallResult(accepted, job_run_ref, response)`

### 15.2 Tests
- **Unit:**
  - Template compile/match (every §9.1 rule, including separator and ambiguity rules, and generated-name overlap tests).
  - `resolution_engine` (every §8 row).
  - Eligibility (every §11.2 cell).
  - Hold arithmetic (SLA 1 / 2 / N, timezone boundaries).
  - `Btch_ID` / `Seq`, period strategies (month ends, leap day, DST) and the status guard (once Q-01 is answered).
- **Integration** (Postgres in Docker with the Appendix A DDL): one named test per ✔ row in §13; the Appendix C walkthrough replayed with `--as-of`; a negative test for every constraint.
- **Engine parity:** pandas vs Spark on the same fixtures.
- **Concurrency:** ingest vs trigger, two ingests on one batch, decisions vs ingest, and a crash/replay of a trigger.
- **Connector:** fake Glue and HTTP servers covering accept, reject, timeout and lost response.
- **Coverage:** 100% branch coverage on `resolution_engine`, `promoter`, `override_manager`, `decision_processor`, `control` and `trigger`.

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
- **Security:**
  - SSE-KMS on S3; RDS encryption and TLS; Secrets Manager for DB and API credentials.
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
- **O-04** became Q-01.
- **O-14** was replaced by D-39–D-41.
- **O-15** was answered by D-48.

### 16.2 Still open

| # | Question | Blocks | Designer's proposal (not assumed) |
|---|---|---|---|
| **Q-01** | The `Req_Stat` value list (you said you'll provide it). Map each value to the §6.1 abstract states. | **`common/status.py`**, CRC writes | — |
| **Q-02** | Supported file types (`.txt` / `.csv` / `.xlsx` / `.parquet`?). Delimiter, quote and escape characters; encoding; for xlsx, sheet name and header row; trailer record layout and whether its count must equal the data rows. | **`file_reader`** | csv/txt only in the first release |
| **Q-03** | Filename matching: case-sensitive or not? Allowed characters in `{RUNTY}`? Any other placeholders? Are files only at the inbound prefix root, or also in sub-folders? | **`templates`** | Case-sensitive; `[A-Za-z0-9]`; root only |
| **Q-04** | Which date decides whether a crosswalk row (and its compliance version) is effective: for files, the report start or end date; for scheduled batches, the scheduled date? | **`filename_matcher`**, scheduler | File: `Rpt_Start_Dt_Key`; scheduler: scheduled date |
| **Q-05** | An ADHOC intake adds a new source to a period whose extract row already exists or was already triggered. Allow it (raise `Required_Src_Cnt`, mark re-trigger required) or reject it? | intake | Allow before the trigger; reject after |
| **Q-06** | STRICT: when completeness or rules are satisfied only through approved waivers, is the trigger automatic or manual? | `control` | Manual (`MANUAL_ONLY`) |
| **Q-07** | Extract call: retry count and backoff on failure. Can the job/API accept a `Trigger_ID` for idempotency? Can the framework query a call's status after a crash? | **`trigger`** | 3 retries, exponential backoff; `TRIGGER_ID` param |
| **Q-08** | HTTP API authentication (IAM SigV4, API key in Secrets Manager, OAuth client credentials), timeout, and which response codes count as "accepted". | `connectors/http_api` | — |
| **Q-09** | After a reopen, if the extract is no longer AUTO-eligible (e.g. STRICT rules now fail), is an alert-only response right? | `evaluator` | Alert only |
| **Q-10** | Zero-byte file when no header is expected: treat it as a zero-record file (D-60 applies)? | `ingest` | Yes |
| **Q-11** | RDS PostgreSQL version, and is the `btree_gist` extension allowed? | DDL | ≥ 14 with `btree_gist` |
| **Q-12** | GRE call mechanics: import it as a Python library or call a GRE entry point? Naming for rule group and variant per (project, table, source, scope)? Which GRE tables or columns give pass/fail per rule and the completion marker? | **`gre_adapter`** | Library call; group = `project.table`, variant = `src|scope` |
| **Q-13** | Phase-2 orchestration choice (D-13). | Phase 2 only | — |
| **Q-14** | Migration: order of existing processes, parallel-run period, catch-up lookback days and go-live date per project. | Rollout, catch-up | — |
| **Q-15** | Data classification (PHI) per source, KMS keys, and who holds `framework_approver`. | Security | — |
| **Q-16** | Auto re-trigger after a correction may regenerate or resubmit an extract already sent externally. Is that acceptable, or should post-submission re-triggers always be manual? | `evaluator` | Manual after the first successful trigger (would amend D-41) |
| **Q-17** | Should extracts past `Earliest_Trigger_Dt` and still not triggered raise an alert, and after how many days? | ops | Alert after 1 day |
| **Q-18** | CYCLE_INIT for a period whose batches already exist: skip silently (`PROCESSED`) or fail? | intake | Skip, logged |

---

## 17. Change Log (v2 → v3)

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

> **Source of truth:** `src/framework/sql/ddl/001_schema.sql` in the repository. Seed data lives in `src/framework/sql/seed/` (event types, period strategies, and **provisional** `Req_Stat` values pending Q-01). The implementation added `Failed_Rule_Refs`, `Data_Signature` and `Triggered_Data_Signature` to `ComplianceExtractControl`.

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;          -- Q-11
CREATE SCHEMA IF NOT EXISTS cms_compliance;
SET search_path = cms_compliance, public;

-- ================= lookups =================
CREATE TABLE ComplianceSourceSystem (
  Src_Cd       VARCHAR(30)  PRIMARY KEY,
  Src_Nm       VARCHAR(100) NOT NULL,
  Src_Ty       VARCHAR(20)  NOT NULL CHECK (Src_Ty IN ('VENDOR','INTERNAL')),
  Active_Ind   SMALLINT     NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user
);

CREATE TABLE ComplianceRunType (
  Run_Ty          VARCHAR(20)  PRIMARY KEY CHECK (Run_Ty ~ '^[A-Za-z0-9]+$'),   -- must be usable as {RUNTY} (Q-03)
  Run_Ty_Desc     VARCHAR(200) NOT NULL,
  Run_Category_Cd VARCHAR(10)  NOT NULL CHECK (Run_Category_Cd IN ('ROUTINE','ADHOC')),
  SLA_Days        INT          NOT NULL CHECK (SLA_Days >= 1),                  -- D-38 hold
  Active_Ind      SMALLINT     NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user
);

CREATE TABLE ComplianceRequestStatus (                 -- values: Q-01
  Req_Stat        VARCHAR(30)  PRIMARY KEY,
  Req_Stat_Desc   VARCHAR(200) NOT NULL,
  Abstract_State  VARCHAR(25)  NOT NULL CHECK (Abstract_State IN ('S_AWAITING','S_VALIDATED','S_PROMOTED',
                    'S_EXCEPTION','S_COMPLETE','S_COMPLETE_EXCEPTION','S_NOT_PROVIDED')),
  Is_Closed_Ind   SMALLINT     NOT NULL CHECK (Is_Closed_Ind IN (0,1)),
  Active_Ind      SMALLINT     NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1))
);

CREATE TABLE ComplianceRequestStatusTransition (
  From_Req_Stat VARCHAR(30) NOT NULL REFERENCES ComplianceRequestStatus,
  To_Req_Stat   VARCHAR(30) NOT NULL REFERENCES ComplianceRequestStatus,
  Trigger_Cd    VARCHAR(40) NOT NULL,
  PRIMARY KEY (From_Req_Stat, To_Req_Stat, Trigger_Cd)
);

CREATE TABLE ComplianceEventType (
  Event_Ty          VARCHAR(60) PRIMARY KEY,
  Log_Tbl_Cd        VARCHAR(20) NOT NULL CHECK (Log_Tbl_Cd IN ('FILE_DETAIL','EXCEPTIONS_AUDIT')),
  Event_Ctgy        VARCHAR(20) NOT NULL CHECK (Event_Ctgy IN ('AUDIT','EXCEPTION')),
  Default_Sevrty    VARCHAR(10) NOT NULL CHECK (Default_Sevrty IN ('INFO','WARNING','ERROR')),
  Notify_Ind        SMALLINT    NOT NULL DEFAULT 0 CHECK (Notify_Ind IN (0,1)),
  Notify_Channel_Cd VARCHAR(10) NOT NULL DEFAULT 'SES' CHECK (Notify_Channel_Cd IN ('SES','SNS','BOTH')),
  Event_Desc        VARCHAR(300)
);

CREATE TABLE CompliancePeriodStrategy (
  Period_Strategy_Cd          VARCHAR(40)  PRIMARY KEY,
  Sql_File_Nm                 VARCHAR(200) NOT NULL,
  Requires_Lookback_Days_Ind  SMALLINT NOT NULL DEFAULT 0 CHECK (Requires_Lookback_Days_Ind IN (0,1)),
  Requires_Lookback_Weeks_Ind SMALLINT NOT NULL DEFAULT 0 CHECK (Requires_Lookback_Weeks_Ind IN (0,1)),
  Strategy_Desc               VARCHAR(300)
);

-- ================= configuration =================
CREATE TABLE ComplianceDataSetSourceXwalk (
  Project_Cd         VARCHAR(30) NOT NULL,
  Table_Nm           VARCHAR(63) NOT NULL,                              -- physical core table (D-25)
  Src_Cd             VARCHAR(30) NOT NULL REFERENCES ComplianceSourceSystem,
  Run_Ty             VARCHAR(20) NOT NULL REFERENCES ComplianceRunType,
  Effective_Start_Dt DATE        NOT NULL,
  Effective_End_Dt   DATE,
  Cmplnc_Vrsn        VARCHAR(10) NOT NULL,                              -- D-51
  Period_Strategy_Cd VARCHAR(40) REFERENCES CompliancePeriodStrategy,   -- ROUTINE only (validator)
  Lookback_Days      INT CHECK (Lookback_Days > 0),
  Lookback_Weeks     INT CHECK (Lookback_Weeks > 0),
  Schedule_Cron_Expr VARCHAR(100),                                      -- ROUTINE only (validator)
  Business_Tz        VARCHAR(40) NOT NULL,
  Active_Ind         SMALLINT    NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user,
  PRIMARY KEY (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt),
  CHECK (Effective_End_Dt IS NULL OR Effective_End_Dt >= Effective_Start_Dt),
  CHECK (Period_Strategy_Cd IS DISTINCT FROM 'PREV_N_DAYS'        OR Lookback_Days  IS NOT NULL),
  CHECK (Period_Strategy_Cd IS DISTINCT FROM 'PREV_WEEK_SAME_DAY' OR Lookback_Weeks IS NOT NULL),
  CONSTRAINT ex_xwalk_no_overlap EXCLUDE USING gist (
    Project_Cd WITH =, Table_Nm WITH =, Src_Cd WITH =, Run_Ty WITH =,
    daterange(Effective_Start_Dt, Effective_End_Dt, '[]') WITH &&) WHERE (Active_Ind = 1)
);

CREATE TABLE ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_Cd                 VARCHAR(30)  NOT NULL REFERENCES ComplianceSourceSystem,
  Src_File_Nm_Tmplt      VARCHAR(255) NOT NULL,                         -- §9 (validator checks grammar)
  Project_Alias          VARCHAR(50)  NOT NULL,
  Table_Alias            VARCHAR(80)  NOT NULL,
  Src_Alias              VARCHAR(50)  NOT NULL,
  Src_File_Ty            VARCHAR(10)  NOT NULL,                         -- allowed set: Q-02
  Delmtr_Cd              VARCHAR(5),
  Line_Term_Cd           VARCHAR(5),
  Src_File_Has_Hdr_Ind   SMALLINT NOT NULL CHECK (Src_File_Has_Hdr_Ind IN (0,1)),
  Src_File_Has_Trlr_Ind  SMALLINT NOT NULL CHECK (Src_File_Has_Trlr_Ind IN (0,1)),
  Allow_Zero_Rcd_Ind     SMALLINT NOT NULL CHECK (Allow_Zero_Rcd_Ind IN (0,1)),         -- D-60
  Engine_Cd              VARCHAR(10) NOT NULL CHECK (Engine_Cd IN ('PANDAS','SPARK')),  -- D-62
  Rules_Vld_Md           VARCHAR(10) NOT NULL CHECK (Rules_Vld_Md IN ('GATE','ANNOTATE')), -- D-44
  Is_Rules_Engine_Required SMALLINT NOT NULL DEFAULT 1 CHECK (Is_Rules_Engine_Required IN (0,1)),
  Load_Exclude_Col_List  TEXT,                                          -- D-61 (optional)
  S3_Src_File_Path       VARCHAR(500) NOT NULL,
  Src_File_Archive_Path  VARCHAR(500) NOT NULL,
  S3_Quarantine_Path     VARCHAR(500) NOT NULL,
  Stg_Schema_Nm          VARCHAR(63)  NOT NULL,
  Stg_Tblnm              VARCHAR(63)  NOT NULL,
  Core_Schema_Nm         VARCHAR(63)  NOT NULL,
  Core_Tblnm             VARCHAR(63)  NOT NULL,
  Bus_Email_Id           VARCHAR(500),
  Bus_Usr_Grp_Nm         VARCHAR(100),
  Dlvry_Ownr_Grp_Nm      VARCHAR(100),
  Sucs_Email_Notfn_Id    TEXT,
  Failr_Email_Notfn_Id   TEXT,
  Email_Subjct_Txt       TEXT,
  Email_Cntnt_Txt        TEXT,
  Notify_Channel_Cd      VARCHAR(10) CHECK (Notify_Channel_Cd IN ('SES','SNS','BOTH')),  -- overrides event default
  Sns_Topic_Arn          VARCHAR(300),
  Active_Ind             SMALLINT NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user,
  CHECK (Core_Tblnm = Table_Nm),
  CHECK (Notify_Channel_Cd IS DISTINCT FROM 'SNS' OR Sns_Topic_Arn IS NOT NULL),
  CHECK (Notify_Channel_Cd IS DISTINCT FROM 'BOTH' OR Sns_Topic_Arn IS NOT NULL)
);
CREATE UNIQUE INDEX ux_filecfg_one_active ON ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_Cd) WHERE Active_Ind = 1;
CREATE UNIQUE INDEX ux_filecfg_alias_active ON ComplianceSourceFileConfig (Project_Alias, Table_Alias, Src_Alias) WHERE Active_Ind = 1;

CREATE TABLE ComplianceExtractPolicy (
  Project_Cd            VARCHAR(30) NOT NULL,
  Table_Nm              VARCHAR(63) NOT NULL,
  Run_Ty                VARCHAR(20) NOT NULL REFERENCES ComplianceRunType,
  Extract_Gating_Md     VARCHAR(20) NOT NULL DEFAULT 'STRICT_ALL_PASS'
                        CHECK (Extract_Gating_Md IN ('STRICT_ALL_PASS','BEST_EFFORT')),
  Period_Rules_Vld_Md   VARCHAR(10) NOT NULL DEFAULT 'GATE' CHECK (Period_Rules_Vld_Md IN ('GATE','ANNOTATE')),  -- D-63
  Extract_Job_Ty        VARCHAR(10) NOT NULL CHECK (Extract_Job_Ty IN ('GLUE_JOB','HTTP_API')),
  Extract_Job_Nm        VARCHAR(255),                                   -- GLUE_JOB
  Extract_Endpoint_Url  VARCHAR(1000),                                  -- HTTP_API
  Extract_Http_Method   VARCHAR(10) CHECK (Extract_Http_Method IN ('POST','PUT')),
  Auth_Secret_Nm        VARCHAR(255),                                   -- Q-08
  Call_Timeout_Sec      INT NOT NULL DEFAULT 60 CHECK (Call_Timeout_Sec > 0),
  Max_Call_Retry_Cnt    INT NOT NULL DEFAULT 0 CHECK (Max_Call_Retry_Cnt >= 0),   -- Q-07
  Active_Ind            SMALLINT NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user,
  PRIMARY KEY (Project_Cd, Table_Nm, Run_Ty),
  CHECK (Extract_Job_Ty <> 'GLUE_JOB' OR Extract_Job_Nm IS NOT NULL),
  CHECK (Extract_Job_Ty <> 'HTTP_API' OR (Extract_Endpoint_Url IS NOT NULL AND Extract_Http_Method IS NOT NULL))
);

CREATE TABLE ComplianceExtractJobParam (
  Project_Cd    VARCHAR(30)  NOT NULL,
  Table_Nm      VARCHAR(63)  NOT NULL,
  Run_Ty        VARCHAR(20)  NOT NULL,
  Param_Nm      VARCHAR(100) NOT NULL,                                  -- e.g. '--PERIOD_START' or JSON field name
  Param_Src_Cd  VARCHAR(15)  NOT NULL CHECK (Param_Src_Cd IN ('LITERAL','EXTRACT_ATTR','BTCH_ID_LIST','LOAD_ID_LIST','TRIGGER_ID')),
  Param_Val     VARCHAR(1000),                                          -- literal, or attribute name for EXTRACT_ATTR
  Param_Seq     INT NOT NULL DEFAULT 1,
  PRIMARY KEY (Project_Cd, Table_Nm, Run_Ty, Param_Nm),
  FOREIGN KEY (Project_Cd, Table_Nm, Run_Ty) REFERENCES ComplianceExtractPolicy,
  CHECK (Param_Src_Cd NOT IN ('LITERAL','EXTRACT_ATTR') OR Param_Val IS NOT NULL)
);

CREATE TABLE ComplianceRuleBinding (                                    -- call details Q-12
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_Cd           VARCHAR(30)  NOT NULL,                               -- '*' for PERIOD_LEVEL
  Rule_Scope_Cd    VARCHAR(20)  NOT NULL CHECK (Rule_Scope_Cd IN ('FILE_LEVEL','PERIOD_LEVEL')),
  Gre_Rule_Group   VARCHAR(100) NOT NULL,
  Gre_Rule_Variant VARCHAR(100) NOT NULL,
  Active_Ind       SMALLINT NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  PRIMARY KEY (Project_Cd, Table_Nm, Src_Cd, Rule_Scope_Cd, Gre_Rule_Group, Gre_Rule_Variant),
  CHECK ((Rule_Scope_Cd = 'PERIOD_LEVEL') = (Src_Cd = '*'))
);

-- ================= control =================
CREATE TABLE ComplianceRequestInTake (
  Intake_ID        VARCHAR(50)  PRIMARY KEY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL REFERENCES ComplianceRunType,
  Src_Cd           VARCHAR(30)  REFERENCES ComplianceSourceSystem,
  Req_Ty           VARCHAR(30)  NOT NULL CHECK (Req_Ty IN ('CYCLE_INIT','ADHOC_REQUEST','CORRECTION_REQUEST')),
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Rsn              TEXT,
  Requested_By     VARCHAR(100) NOT NULL,
  Requested_Dtts   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Intake_Stat      VARCHAR(20)  NOT NULL DEFAULT 'NEW'
                   CHECK (Intake_Stat IN ('NEW','PROCESSED','PARTIALLY_PROCESSED','FAILED')),
  Processed_Dtts   TIMESTAMPTZ,
  Error_Txt        TEXT,
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Req_Ty <> 'CORRECTION_REQUEST' OR Src_Cd IS NOT NULL)
);

CREATE TABLE ComplianceExtractControl (
  Extract_ID                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd                VARCHAR(30) NOT NULL,
  Table_Nm                  VARCHAR(63) NOT NULL,
  Run_Ty                    VARCHAR(20) NOT NULL REFERENCES ComplianceRunType,
  Rpt_Start_Dt_Key          DATE NOT NULL,
  Rpt_End_Dt_Key            DATE NOT NULL,
  Required_Src_Cnt          INT  NOT NULL CHECK (Required_Src_Cnt >= 0),
  Received_Src_Cnt          INT  NOT NULL DEFAULT 0,
  Waived_Src_Cnt            INT  NOT NULL DEFAULT 0,
  Included_Src_Cds TEXT, Missing_Src_Cds TEXT, Waived_Src_Cds TEXT,
  Extract_Stat              VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (Extract_Stat IN ('PENDING','PARTIAL','COMPLETE')),
  Extract_Rules_Stat        VARCHAR(25) NOT NULL DEFAULT 'PENDING'
                            CHECK (Extract_Rules_Stat IN ('PENDING','PASSED','PASSED_WITH_WARNINGS','FAILED','ERROR')),
  Earliest_Trigger_Dt       DATE NOT NULL,
  Eligibility_Cd            VARCHAR(15) NOT NULL DEFAULT 'NOT_ELIGIBLE'
                            CHECK (Eligibility_Cd IN ('NOT_ELIGIBLE','MANUAL_ONLY','AUTO')),
  Eligibility_Rsn_Txt       VARCHAR(1000),
  Trigger_Stat              VARCHAR(15) NOT NULL DEFAULT 'NOT_TRIGGERED'
                            CHECK (Trigger_Stat IN ('NOT_TRIGGERED','REQUESTED','TRIGGERED','FAILED')),
  Last_Trigger_ID           BIGINT,                                     -- FK added below
  Trigger_Cnt               INT NOT NULL DEFAULT 0,
  Triggered_Combine_Run_Nbr INT,
  Retrigger_Required_Ind    SMALLINT NOT NULL DEFAULT 0 CHECK (Retrigger_Required_Ind IN (0,1)),
  Extract_Close_Ind         SMALLINT NOT NULL DEFAULT 0 CHECK (Extract_Close_Ind IN (0,1)),
  Extract_Closed_Dtts       TIMESTAMPTZ,
  Combine_Run_Cnt           INT NOT NULL DEFAULT 0,
  Combine_Last_Run_Dtts     TIMESTAMPTZ,
  Combine_Last_Trigger_Cd   VARCHAR(20) CHECK (Combine_Last_Trigger_Cd IN
                              ('EARLY_COMPLETE','SLA_EVALUATION','MANUAL_TRIGGER','REOPEN_PROMOTED','WAIVER_DECISION','MANUAL_REFRESH')),
  Combine_Btch_ID_List      TEXT,
  Failed_Rule_Refs          TEXT,          -- GATE-failed period rules of the last combine
  Data_Signature            CHAR(64),      -- hash of (Btch_ID, Current_Load_ID) at the last combine
  Triggered_Data_Signature  CHAR(64),      -- Data_Signature at the last successful trigger (drives Retrigger_Required_Ind)
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key),
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Extract_Close_Ind = 0 OR (Trigger_Cnt >= 1 AND Extract_Closed_Dtts IS NOT NULL))
);

CREATE TABLE ComplianceRequestControl (
  Req_ID               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd           VARCHAR(30)  NOT NULL,
  Table_Nm             VARCHAR(63)  NOT NULL,
  Src_Cd               VARCHAR(30)  NOT NULL REFERENCES ComplianceSourceSystem,
  Run_Ty               VARCHAR(20)  NOT NULL REFERENCES ComplianceRunType,
  Rpt_Start_Dt_Key     DATE         NOT NULL,
  Rpt_End_Dt_Key       DATE         NOT NULL,
  Req_Dt_Key           DATE         NOT NULL,                           -- actual creation date (D-29)
  Earliest_Close_Dt    DATE         NOT NULL,                           -- D-38
  Btch_ID              VARCHAR(250) NOT NULL UNIQUE,
  Cmplnc_Vrsn          VARCHAR(10)  NOT NULL,
  Extract_ID           BIGINT       NOT NULL REFERENCES ComplianceExtractControl,
  Intake_ID            VARCHAR(50)  REFERENCES ComplianceRequestInTake,  -- origin only, not grain
  Req_Stat             VARCHAR(30)  NOT NULL REFERENCES ComplianceRequestStatus,
  Resolution_Ty        VARCHAR(10)  CHECK (Resolution_Ty IN ('NEW_FILE','MISSING')),
  Current_Load_ID      BIGINT,                                          -- FK added below
  Batch_Close_Ind      SMALLINT     NOT NULL DEFAULT 0 CHECK (Batch_Close_Ind IN (0,1)),
  Closed_By_Trigger_ID BIGINT,                                          -- FK added below
  Created_By           VARCHAR(15)  NOT NULL CHECK (Created_By IN ('SCHEDULER','CATCHUP','CYCLE_INIT','ADHOC_INTAKE')),
  Created_Dtts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_Dtts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key),        -- D-30
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Earliest_Close_Dt >= Req_Dt_Key),
  CHECK (Resolution_Ty IS DISTINCT FROM 'NEW_FILE' OR Current_Load_ID IS NOT NULL),
  CHECK (Resolution_Ty IS DISTINCT FROM 'MISSING'  OR Current_Load_ID IS NULL),
  CHECK (Batch_Close_Ind = 0 OR (Resolution_Ty IS NOT NULL AND Closed_By_Trigger_ID IS NOT NULL)),
  CHECK (Created_By NOT IN ('CYCLE_INIT','ADHOC_INTAKE') OR Intake_ID IS NOT NULL)
);
CREATE INDEX ix_crc_open    ON ComplianceRequestControl (Extract_ID) WHERE Batch_Close_Ind = 0;
CREATE INDEX ix_crc_extract ON ComplianceRequestControl (Extract_ID);

CREATE TABLE ComplianceFileLoad (
  Load_ID                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  S3_Bucket               VARCHAR(100)  NOT NULL,
  S3_Key                  VARCHAR(1024) NOT NULL,
  S3_Version_Id           VARCHAR(200),
  S3_ETag                 VARCHAR(200)  NOT NULL,
  File_Size_Byte          BIGINT        NOT NULL,
  File_Sha256             CHAR(64),
  Received_Dtts           TIMESTAMPTZ   NOT NULL DEFAULT now(),
  Parsed_Project_Alias    VARCHAR(50), Parsed_Table_Alias VARCHAR(80), Parsed_Src_Alias VARCHAR(50),
  Parsed_Run_Ty           VARCHAR(20), Parsed_Rpt_Start_Dt_Key DATE, Parsed_Rpt_End_Dt_Key DATE,
  Parsed_File_Ts          TIMESTAMP,
  Cfg_ID                  BIGINT REFERENCES ComplianceSourceFileConfig,
  Req_ID                  BIGINT REFERENCES ComplianceRequestControl,
  Btch_ID                 VARCHAR(250),
  Load_Stat               VARCHAR(25) NOT NULL CHECK (Load_Stat IN ('RECEIVED','QUARANTINED','STAGING','STAGED',
                            'RULES_RUNNING','RULES_FAILED','PENDING_APPROVAL','PROMOTED','SUPERSEDED','FAILED_TECHNICAL')),
  Rules_Stat              VARCHAR(25) NOT NULL DEFAULT 'NOT_RUN'
                          CHECK (Rules_Stat IN ('NOT_RUN','PASSED','PASSED_WITH_WARNINGS','FAILED','ERROR')),
  Quarantine_Rsn_Cd       VARCHAR(60) REFERENCES ComplianceEventType,
  Src_Rcd_Cnt BIGINT, Trlr_Rcd_Cnt BIGINT, Stg_Rcd_Cnt BIGINT, Core_Appended_Cnt BIGINT, Core_Disabled_Cnt BIGINT,
  Attempt_Cnt             INT NOT NULL DEFAULT 0,
  Heartbeat_Dtts          TIMESTAMPTZ,
  Promoted_Dtts           TIMESTAMPTZ,
  Error_Txt               TEXT,
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (Load_Stat <> 'QUARANTINED' OR Quarantine_Rsn_Cd IS NOT NULL),
  CHECK (Load_Stat IN ('RECEIVED','QUARANTINED') OR (Req_ID IS NOT NULL AND Btch_ID IS NOT NULL))
);
CREATE UNIQUE INDEX ux_fileload_s3obj ON ComplianceFileLoad (S3_Bucket, S3_Key, COALESCE(S3_Version_Id, S3_ETag));
CREATE INDEX ix_fileload_btch ON ComplianceFileLoad (Btch_ID, Load_Stat);
CREATE INDEX ix_fileload_sha  ON ComplianceFileLoad (File_Sha256);

CREATE TABLE ComplianceExtractTrigger (
  Trigger_ID           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Extract_ID           BIGINT NOT NULL REFERENCES ComplianceExtractControl,
  Trigger_Ty           VARCHAR(10) NOT NULL CHECK (Trigger_Ty IN ('AUTO','MANUAL','RETRIGGER')),
  Requested_By         VARCHAR(100) NOT NULL,
  Warning_Txt          TEXT,
  Ack_Warnings_Ind     SMALLINT NOT NULL DEFAULT 0 CHECK (Ack_Warnings_Ind IN (0,1)),
  Combine_Run_Nbr      INT NOT NULL,
  Rendered_Params_Txt  TEXT NOT NULL,
  Call_Stat            VARCHAR(10) NOT NULL DEFAULT 'REQUESTED' CHECK (Call_Stat IN ('REQUESTED','SUCCEEDED','FAILED')),
  Job_Run_Ref          VARCHAR(300),
  Response_Txt         TEXT,
  Requested_Dtts       TIMESTAMPTZ NOT NULL DEFAULT now(),
  Completed_Dtts       TIMESTAMPTZ,
  CHECK (Warning_Txt IS NULL OR Ack_Warnings_Ind = 1 OR Trigger_Ty <> 'MANUAL'),
  CHECK (Call_Stat = 'REQUESTED' OR Completed_Dtts IS NOT NULL)
);
CREATE UNIQUE INDEX ux_trigger_one_inflight ON ComplianceExtractTrigger (Extract_ID) WHERE Call_Stat = 'REQUESTED';

ALTER TABLE ComplianceExtractControl ADD CONSTRAINT fk_extract_last_trigger FOREIGN KEY (Last_Trigger_ID) REFERENCES ComplianceExtractTrigger;
ALTER TABLE ComplianceRequestControl ADD CONSTRAINT fk_crc_current_load  FOREIGN KEY (Current_Load_ID) REFERENCES ComplianceFileLoad;
ALTER TABLE ComplianceRequestControl ADD CONSTRAINT fk_crc_close_trigger FOREIGN KEY (Closed_By_Trigger_ID) REFERENCES ComplianceExtractTrigger;

CREATE TABLE ComplianceBatchOverride (
  Ovrd_ID              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Override_Ty          VARCHAR(25) NOT NULL CHECK (Override_Ty IN
                         ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN','SOURCE_WAIVER','RULE_WAIVER')),
  Req_ID               BIGINT REFERENCES ComplianceRequestControl,
  Extract_ID           BIGINT NOT NULL REFERENCES ComplianceExtractControl,
  Project_Cd           VARCHAR(30) NOT NULL,
  Table_Nm             VARCHAR(63) NOT NULL,
  Src_Cd               VARCHAR(30),
  Run_Ty               VARCHAR(20) NOT NULL,
  Rpt_Start_Dt_Key     DATE NOT NULL,
  Rpt_End_Dt_Key       DATE NOT NULL,
  Btch_ID              VARCHAR(250),
  Candidate_Load_ID    BIGINT REFERENCES ComplianceFileLoad,
  Reviewed_Load_ID     BIGINT REFERENCES ComplianceFileLoad,
  Prior_Load_ID        BIGINT REFERENCES ComplianceFileLoad,
  Prior_Resolution_Ty  VARCHAR(10),
  Prior_Req_Stat       VARCHAR(30),
  Rule_Ref             VARCHAR(200),
  Apprvl_Stat          VARCHAR(20) NOT NULL DEFAULT 'PENDING_REVIEW'
                       CHECK (Apprvl_Stat IN ('PENDING_REVIEW','APPROVED','REJECTED','REVOKED')),
  Apprvd_By  VARCHAR(100), Apprvd_Dtts  TIMESTAMPTZ,
  Rejected_By VARCHAR(100), Rejected_Dtts TIMESTAMPTZ, Rejection_Rsn  TEXT,
  Revoked_By  VARCHAR(100), Revoked_Dtts  TIMESTAMPTZ, Revocation_Rsn TEXT,
  Promotion_Stat       VARCHAR(15) NOT NULL DEFAULT 'NOT_APPLICABLE'
                       CHECK (Promotion_Stat IN ('NOT_APPLICABLE','PENDING','PROMOTED','FAILED')),
  Promoted_Dtts        TIMESTAMPTZ,
  Last_Processed_Apprvl_Stat VARCHAR(20),
  History              TEXT NOT NULL,
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  Updated_By   VARCHAR(100) NOT NULL DEFAULT current_user,
  CHECK (Override_Ty = 'RULE_WAIVER' OR (Req_ID IS NOT NULL AND Src_Cd IS NOT NULL)),
  CHECK (Override_Ty <> 'RULE_WAIVER' OR Rule_Ref IS NOT NULL),
  CHECK (Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') OR (Btch_ID IS NOT NULL AND Candidate_Load_ID IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'APPROVED' OR (Apprvd_By IS NOT NULL AND Apprvd_Dtts IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'APPROVED' OR Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') OR Reviewed_Load_ID = Candidate_Load_ID),
  CHECK (Apprvl_Stat <> 'REVOKED'  OR (Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER')
                                     AND Revoked_By IS NOT NULL AND Revoked_Dtts IS NOT NULL AND Revocation_Rsn IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'REJECTED' OR (Rejected_By IS NOT NULL AND Rejected_Dtts IS NOT NULL AND Rejection_Rsn IS NOT NULL))
);
CREATE UNIQUE INDEX ux_ovrd_reopen_active ON ComplianceBatchOverride (Req_ID)
  WHERE Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN') AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE UNIQUE INDEX ux_ovrd_srcwaiver_active ON ComplianceBatchOverride (Req_ID)
  WHERE Override_Ty = 'SOURCE_WAIVER' AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE UNIQUE INDEX ux_ovrd_rulewaiver_active ON ComplianceBatchOverride (Extract_ID, Rule_Ref)
  WHERE Override_Ty = 'RULE_WAIVER' AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE INDEX ix_ovrd_decisions ON ComplianceBatchOverride (Ovrd_ID)
  WHERE Apprvl_Stat IS DISTINCT FROM Last_Processed_Apprvl_Stat;

-- ================= audit =================
CREATE TABLE ComplianceRequestFileDetail (
  Detail_ID         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Req_ID            BIGINT NOT NULL REFERENCES ComplianceRequestControl,
  Btch_ID           VARCHAR(250) NOT NULL,
  Load_ID           BIGINT REFERENCES ComplianceFileLoad,
  Ovrd_ID           BIGINT REFERENCES ComplianceBatchOverride,
  Intake_ID         VARCHAR(50) REFERENCES ComplianceRequestInTake,
  Trigger_ID        BIGINT REFERENCES ComplianceExtractTrigger,
  Event_Ty          VARCHAR(60) NOT NULL REFERENCES ComplianceEventType,
  Entry_Ty          VARCHAR(6)  NOT NULL CHECK (Entry_Ty IN ('AUTO','MANUAL')),
  Received_File_Ref VARCHAR(1100),
  Actor             VARCHAR(100) NOT NULL,
  Detail_Txt        TEXT,
  Event_Dtts        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_filedetail_req ON ComplianceRequestFileDetail (Req_ID, Event_Dtts);

CREATE TABLE CMS_ComplianceExceptionsAudit (
  Event_ID     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Event_Ty     VARCHAR(60) NOT NULL REFERENCES ComplianceEventType,
  Event_Ctgy   VARCHAR(20) NOT NULL CHECK (Event_Ctgy IN ('AUDIT','EXCEPTION')),
  Sevrty       VARCHAR(10) NOT NULL CHECK (Sevrty IN ('INFO','WARNING','ERROR')),
  Project_Cd VARCHAR(30), Table_Nm VARCHAR(63), Src_Cd VARCHAR(30), Run_Ty VARCHAR(20),
  Req_ID       BIGINT REFERENCES ComplianceRequestControl,
  Load_ID      BIGINT REFERENCES ComplianceFileLoad,
  Ovrd_ID      BIGINT REFERENCES ComplianceBatchOverride,
  Extract_ID   BIGINT REFERENCES ComplianceExtractControl,
  Intake_ID    VARCHAR(50) REFERENCES ComplianceRequestInTake,
  Trigger_ID   BIGINT REFERENCES ComplianceExtractTrigger,
  Btch_ID      VARCHAR(250),
  File_Ref     VARCHAR(1100),
  Actor        VARCHAR(100) NOT NULL,
  Event_Dtts   TIMESTAMPTZ NOT NULL DEFAULT now(),
  Description  TEXT,
  Notified_Ind SMALLINT NOT NULL DEFAULT 0 CHECK (Notified_Ind IN (0,1))
);
CREATE INDEX ix_excaudit_btch   ON CMS_ComplianceExceptionsAudit (Btch_ID);
CREATE INDEX ix_excaudit_notify ON CMS_ComplianceExceptionsAudit (Event_ID) WHERE Notified_Ind = 0;

CREATE VIEW vw_crc_current_flags AS
SELECT c.Req_ID,
       EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                WHERE d.Req_ID = c.Req_ID AND d.Event_Ty = 'LATE_ARRIVAL_RECEIVED') AS Was_Late_Arrival,
       COALESCE((SELECT d.Event_Ty FROM ComplianceRequestFileDetail d
                  WHERE d.Req_ID = c.Req_ID AND d.Event_Ty IN ('CORRECTION_FLAGGED','CORRECTION_FLAG_CLEARED')
                  ORDER BY d.Event_Dtts DESC, d.Detail_ID DESC LIMIT 1) = 'CORRECTION_FLAGGED', FALSE)
         AS Currently_Flagged_For_Correction
  FROM ComplianceRequestControl c;

-- ================= target-table framework columns (per staging / core table) =================
-- ALTER TABLE <stg>  ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Src_File_Nm VARCHAR(1024) NOT NULL, ADD COLUMN Stg_Load_Dtts TIMESTAMPTZ NOT NULL;
-- CREATE INDEX ON <stg> (Btch_ID, Load_ID);
-- ALTER TABLE <core> ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Current_Ind SMALLINT NOT NULL, ADD COLUMN Load_Dtts TIMESTAMPTZ NOT NULL,
--                    ADD COLUMN End_Dtts TIMESTAMPTZ;
-- CREATE INDEX ON <core> (Btch_ID) WHERE Current_Ind = 1;  CREATE INDEX ON <core> (Load_ID);
```

**Cross-table rules the application enforces** (not expressible as FKs):
- A CRC row's (project, table, source, run type) must have a crosswalk row effective on its scheduled or report date (Q-04).
- A CRC row's `Extract_ID` must have the same (project, table, run type, period).
- An override's denormalized grain must equal its batch's or extract's grain.

---

## Appendix B: Approval / Waiver SQL Templates (D-12, D-48, D-64)

Run these as `framework_approver`. **Each must report 1 row.** A result of 0 means the candidate changed or the row was already decided; re-review.

```sql
-- Approve a reopen (you reviewed load :reviewed_load_id)
UPDATE cms_compliance.ComplianceBatchOverride
   SET Apprvl_Stat='APPROVED', Apprvd_By=:me, Apprvd_Dtts=now(), Reviewed_Load_ID=:reviewed_load_id,
       History = History || E'\n' || now() || ' APPROVED by ' || :me, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
   AND Apprvl_Stat='PENDING_REVIEW' AND Candidate_Load_ID=:reviewed_load_id;

-- Reject a pending reopen or waiver
UPDATE cms_compliance.ComplianceBatchOverride
   SET Apprvl_Stat='REJECTED', Rejected_By=:me, Rejected_Dtts=now(), Rejection_Rsn=:reason,
       History = History || E'\n' || now() || ' REJECTED by ' || :me || ': ' || :reason, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Apprvl_Stat='PENDING_REVIEW';

-- Request a SOURCE_WAIVER for an open batch
INSERT INTO cms_compliance.ComplianceBatchOverride
  (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, History)
SELECT 'SOURCE_WAIVER', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
       now() || ' SOURCE_WAIVER requested by ' || :me || ': ' || :reason
  FROM cms_compliance.ComplianceRequestControl WHERE Req_ID=:req_id AND Batch_Close_Ind=0;

-- Request a RULE_WAIVER for an extract
INSERT INTO cms_compliance.ComplianceBatchOverride
  (Override_Ty, Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Rule_Ref, History)
SELECT 'RULE_WAIVER', Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, :rule_ref,
       now() || ' RULE_WAIVER requested by ' || :me || ': ' || :reason
  FROM cms_compliance.ComplianceExtractControl WHERE Extract_ID=:extract_id AND Trigger_Stat <> 'TRIGGERED';

-- Approve a waiver
UPDATE cms_compliance.ComplianceBatchOverride
   SET Apprvl_Stat='APPROVED', Apprvd_By=:me, Apprvd_Dtts=now(),
       History = History || E'\n' || now() || ' APPROVED by ' || :me, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') AND Apprvl_Stat='PENDING_REVIEW';

-- Revoke an approved waiver (only before the extract is triggered)
UPDATE cms_compliance.ComplianceBatchOverride o
   SET Apprvl_Stat='REVOKED', Revoked_By=:me, Revoked_Dtts=now(), Revocation_Rsn=:reason,
       History = History || E'\n' || now() || ' REVOKED by ' || :me || ': ' || :reason, Updated_Dtts=now()
  FROM cms_compliance.ComplianceExtractControl e
 WHERE o.Ovrd_ID=:ovrd_id AND o.Extract_ID=e.Extract_ID AND o.Apprvl_Stat='APPROVED'
   AND o.Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') AND e.Trigger_Stat <> 'TRIGGERED';
```

---

## Appendix C: Synthetic Walkthrough

All names are placeholders.

**Config**
- Project `PRJA`, table `tbl_x`, run type `MONTHLY` (`SLA_Days = 2`, `STRICT_ALL_PASS`, Glue extract job `extract_prja_tbl_x`).
- Sources `S1` and `S2`, both with template `{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt`.
- Aliases: `PRJA` / `TBLX` / `S1` and `PRJA` / `TBLX` / `S2`.

| When | Event | Result |
|---|---|---|
| Feb 1 06:00 | Scheduler fires; period Jan 1–31 | Two batches: `20260201_PRJA_tbl_x_S1_MONTHLY_<v>_1` and `…_S2_…_1`. `Earliest_Close_Dt` = Feb 2. Extract row created with `Required = 2`. |
| Feb 1 09:30 | `PRJA_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt` | Matched; promoted (O-1). Extract `PARTIAL`. |
| Feb 1 11:00 | `PRJA_TBLX_S2_MONTHLY_20260101_20260131_20260201105500.txt` | Promoted. Complete → early combine → period rules `PASSED`. Eligibility `NOT_ELIGIBLE` (hold until Feb 2). |
| Feb 1 12:00 | S1 resend with new `{TS}` and new content | O-2 replacement; early combine re-runs. |
| Feb 2 00:15 | `evaluate-extracts` | Hold passed; complete; rules passed → `AUTO` → Glue job called and accepted → both batches closed (`S_COMPLETE`), extract `TRIGGERED`. |
| Feb 5 | Corrected S2 file | Batch is closed → X-1 `CORRECTION_REOPEN` `PENDING_REVIEW`. |
| Feb 5 | Approver runs the template | P7 promotes; batch stays closed; refresh → `AUTO` → `RETRIGGER` (subject to Q-16). |
| Feb 6 | `PRJA_TBLX_S3_MONTHLY_…` (unknown alias) | C2 `FILE_REJECTED_UNPARSEABLE`. |
| Feb 6 | `PRJA_TBLX_S1_MONTHLY_20260201_20260228_…` (February batch not created yet) | C6 `FILE_REJECTED_NO_BATCH`. |
