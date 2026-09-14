# CMS Compliance Framework — Schema Design

## Purpose
A reusable, metadata-driven framework for CMS compliance data submissions
(Program Audit ODAG/CDAG, Part C ODR, Part C SLIDA, etc.), replacing and
enhancing the existing Teradata-based "Universe Framework." Targets AWS
(Glue, Lambda, Step Functions, EventBridge, S3, RDS for PostgreSQL,
CloudWatch/SNS).

## Naming convention
Follows the existing Teradata standard: `PascalCase_With_Underscores`,
`_Ind` for boolean indicators, `_Dtts` / `_Dt_Tm` for timestamps, `_Nm` for
names, `_Ty` for types, `_Rsn` for reasons, `_Stat` for status, `_Flg` for
flags, `SMALLINT` for booleans.

`Btch_ID` format (creation-date based):
```
{YYYYMMDD}_{Project_Code}_{Dtst_Nm}_{Src_ID}_{Run_Ty}_{Cmplnc_Vrsn}_{Seq}
```
A correction/late-arrival reprocess appends `_v{n}`.

## Core tables

### ComplianceRequestControl ("CRC")
The core, lean, high-read batch-tracking table — one row per
(project, table, source, run_type, request_date). Deliberately kept slim
because it's read everywhere in the pipeline. Key columns:

| Column | Purpose |
|---|---|
| `Req_ID` | Surrogate key |
| `Project_Cd`, `Table_Nm`, `Src_Cd`, `Run_Ty` | Grain |
| `Cycle_ID` | Isolates concurrent execution cycles of the same request period |
| `Req_Dt_Key` | The date this batch represents |
| `Btch_ID`, `Cmplnc_Vrsn` | Batch identity and CMS layout version |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Reporting period covered |
| `Req_Stat` | Status (see domain below) |
| `File_Received_Ind` | Whether a file actually arrived that day |
| `Resolution_Ty` | `NEW_FILE`, `CARRIED_FORWARD`, or `MISSING` |
| `Used_Btch_ID` | Which batch's data was actually consumed — chains to the prior day's batch, not the original anchor, so this table stays populated on every row |
| `Override_Exists_Ind` | Flags whether a row in `ComplianceBatchOverride` exists for this batch |
| `Created_Dtts` | Audit |

`Req_Stat` domain (subset shown in mock data): `RULES_PASSED`,
`EXTRACTED`, `EXCEPTION_PENDING`, `COMPLETED`, `DATA_NOT_PROVIDED`,
`SUPERSEDED` (18 statuses total in the full domain).

### ComplianceBatchOverride ("CRC Override")
Sparse table — a row exists **only** when a batch involves carry-forward
reuse or a correction requiring business approval. Holds the
approval/revocation workflow fields kept off the lean CRC table:

| Column | Purpose |
|---|---|
| `Req_ID`, `Btch_ID` | Ties back to the CRC row that originated the override |
| `Used_Btch_ID` | The anchor batch being reused |
| `Apprvl_Stat` | `PENDING_REVIEW`, `APPROVED`, `REVOKED`, `SUPERSEDED` |
| `Apprvd_By`, `Apprvd_Dtts` | Approval audit |
| `Revoked_By`, `Revoked_Dtts`, `Revocation_Rsn` | Revocation audit |
| `History` | Human-readable append-only log of every status transition for that row |

Updated in place (not append-only) — one row per anchor batch, its status
and audit fields mutate as approval/revocation events happen, with
`History` preserving the trail.

### Supporting reference/metadata tables
`ComplianceSourceSystem`, `ComplianceRunType`,
`ComplianceDataSetSourceXwalk`, `ComplianceRequestInTake`,
`CMS_COMPLIANCE_RULESENGINE`, `CMS_ComplianceExceptionsAudit` — populated
once at onboarding or business-triggered (ad-hoc pulls, new cycles,
corrections), not part of the daily transactional path.

## Mock data scenarios (all combined directly into the CRC / CRC Override
tabs — no separate per-scenario tabs)

- **PARTCODR / ROPENS** — 3 sources × 13 days. Every batch is `NEW_FILE`;
  this table isn't carry-forward eligible, so `Override_Exists_Ind = N`
  throughout and no override rows exist for it.
- **PROGAUDIT / ODAG3** — 7 sources × 13 days. Each source arrives
  incorrectly for several days (`PENDING_REVIEW` override rows), gets the
  correct file on a source-specific day (approved same day, prior pending
  rows superseded), then carries forward for the rest of the period.
- **PROGAUDIT / ODAG1 (7-day / 5-FDR reuse walkthrough)** — includes an
  approve → revoke → re-anchor cycle on one source (FDR 1002), showing a
  carried-forward batch losing its approval mid-stream and the source
  falling back to `MISSING` until a fresh file restores it.

## Open items / not yet covered here
Downstream data-warehouse tables (staging, combined/final, SCD2 output)
are project-specific and out of scope for this control/metadata layer.
