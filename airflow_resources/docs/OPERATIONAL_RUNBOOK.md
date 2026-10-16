# Compliance Batch Framework – Operational Runbook

**Audience:** business analysts, operations and support staff who set up and run projects on the framework, without
reading the code. **Version:** framework 0.3.49 (Airflow 3 / Teradata).

**How to use this page**

- Every SQL block can be copied as it is. Replace the placeholders before running:
  `HSABC_DEV` = your metadata database, `CMSUNIV` = your project code, `ODAG3` = your table (dataset) code, and the
  sample source IDs, names, folders and schemas with yours.
- Nothing is case sensitive: codes (project, table, source, run type, check, value and status codes), column, table
  and database names, file names against their templates, record types, `VALUE_IN` values, `PATTERN`s,
  `DATE_FORMAT`s, period codes, SQL placeholders, Airflow Variable and trigger keys. The framework stores codes in
  upper case; write them in upper case to avoid confusion. (Airflow Connection ids are Airflow's own and keep their
  case.)
- `*` in a code column means "every" (every table, every source, every run type, every field), where the table says
  it is allowed.
- `Active_Ind = 1` means the row is in use; `0` means the row is ignored as if it did not exist. Use `0` instead of
  deleting a configuration row, so the history stays.
- The audit columns `Created_Dtts`, `Created_By`, `Updated_Dtts`, `Updated_By` are filled by the database. Leave
  them out of every INSERT. Columns marked **(framework)** are written by the framework only: never insert or update
  them.
- After any configuration change, run **validate-config** (section 9.1). It reports every problem it finds.

---

## Contents

1. What the framework does (business view)
2. The daily flow (DAGs) – diagram
3. The life of a batch – diagram and statuses
4. Metadata tables – map and roles
5. Configuration tables (you fill these)
6. Request and action tables (you add rows when needed)
7. Tables the framework writes (read only)
8. Reference lists (all codes and values)
9. Airflow: connections, Variables, settings, DAGs, triggers
10. Set up a new project – step by step
11. Operating procedures (how to …)
12. Monitoring queries
13. Troubleshooting
14. Glossary

---

## 1. What the framework does (business view)

The framework collects data for compliance reporting from many **sources** (external vendors such as FDRs that
send files, and internal systems that are read with a query), for many **tables** (datasets, e.g. ODAG3) of a
**project** (e.g. the CMS Universes), on a schedule (**run types** such as DAILY, WEEKLY, MNT) or on request
(**ad hoc**).

For every table, run type and report period it creates a **batch**, waits for each source's data, checks it, loads
it into the table's **staging** and **core** tables, optionally copies it to an **export** table, runs data quality
**rules**, and **closes** the batch when its time is up. Everything that happens is recorded in a **timeline** per
batch and an **exceptions audit**; problems are **emailed**.

What a business user controls, all in metadata tables (no code):

| You want to … | Where |
|---|---|
| Add a project, its tables and sources | `ComplianceProject`, `ComplianceTable`, `ComplianceSourceSystem` |
| Say which source sends which table for which run types, and from when | `ComplianceDataSetSourceXwalk` |
| Say when batches are created and their SLA | `ComplianceRunType` (and optionally `ComplianceBatchCalendar`) |
| Say which report dates each run type's batches cover | `ComplianceRunTypePeriod` |
| Describe a source's file (name, delimiter, folder, header / trailer) | `ComplianceSourceFileConfig` |
| Pull an internal source with a query instead of a file | `ComplianceInternalSourceConfig` |
| Check files before / while loading | `ComplianceFileCheckConfig` |
| Fill derived columns (batch ID, version, load time, row ID …) | `ComplianceTargetColumnConfig` |
| Copy data to an export table | `ComplianceExportConfig` |
| Run data quality rules | `ComplianceRuleBinding` |
| Request an ad-hoc batch | `ComplianceRequestInTake` |
| Reuse data, accept late or corrected data, reopen a batch | `ComplianceBatchOverride` |

---

## 2. The daily flow (DAGs)

Each step is an Airflow DAG with its own Variable (settings). The arrows are triggers: when a step succeeds it
starts the next one (configured in its Variable, `trigger_dags`).

```
 BATCH_CREATION  (daily, e.g. 06:00)
   creates the batches that are due (schedule codes, calendar, ad-hoc requests)
        │
        ▼
 FILE_CHECK  (optional, e.g. every 15 min) ─────┐  checks waiting files, loads nothing
        │                                       │
        ▼                                       │
 FILE_LOAD  (e.g. hourly)        SOURCE_LOAD  (e.g. 06:30)
   files from the NAS              internal sources by query
   → staging → core                → staging → core
        │                               │
        └──────────────┬────────────────┘
                       ▼
                 EXPORT_LOAD  (triggered + daily, e.g. 20:00)
                   core → export table (when due / released)
                       │
                       ▼
                 NOTIFY  ◄──── also triggered by the other steps
                   emails the open audit events

 OVERRIDE_DECISIONS  (triggered by FILE_LOAD, or scheduled)
   applies approved carry-forwards (REUSE) and reopens (REOPEN), expires old ones

 BATCH_CLOSE  (nightly, e.g. 22:00)
   closes every batch past its close date (with or without data)

 Command line (by hand)  validate-config, health, locks, close-batch … (9.5)
 FILE_RULES              data quality rules: no DAG yet (added when the rules are set up)
```

---

## 3. The life of a batch

A batch is one table + run type + report period + request date. Every **source** of the table gets its own row in
`ComplianceRequestControl` under the same batch ID (`Btch_ID`), with its own status.

```
                         BATCH_CREATION
                               │
                               ▼
                       ┌───────────────┐
            ┌──────────│REQUEST_CREATED│──────────────┐
            │          └───────────────┘              │
   file / pull with          │  file with no rows,     │ approved REUSE
   data rows                 │  or pull with no rows   │ (carry-forward)
            ▼                ▼  on/after due date      ▼
 ┌─────────────────┐ ┌─────────────────────────┐ ┌──────────────────────────┐
 │SOURCE_DATA_READY│ │SOURCE_DATA_READY_NO_DATA│ │SOURCE_DATA_CARRY_FORWARD │
 └────────┬────────┘ └────────────┬────────────┘ └────────────┬─────────────┘
          │  BATCH_CLOSE          │ BATCH_CLOSE                │ BATCH_CLOSE
          │  (close date)         │ (close date)               │ (close date)
          ▼                       ▼                            ▼
 ┌────────────────┐     ┌──────────────────┐          ┌────────────────┐
 │REQUEST_COMPLETE│     │DATA_NOT_PROVIDED │          │REQUEST_COMPLETE│
 │  (NEW_FILE)    │     │   (NO_DATA)      │          │(CARRY_FORWARD) │
 └────────────────┘     └──────────────────┘          └────────────────┘

 REQUEST_CREATED with nothing by the close date ──BATCH_CLOSE──► DATA_NOT_PROVIDED (MISSING)
                                                                 + alert SOURCE_MISSING_AT_CLOSE

 Closed batch + approved LATE_ARRIVAL / CORRECTION ──file──► data loaded, batch stays closed
 Closed batch + approved REOPEN ──OVERRIDE_DECISIONS──► open again until new data, then closed again
```

**Close date** (also called due date) = `Req_Dt_Key + SLA_Days - 1`. Example: created Sunday 10/18 with
`SLA_Days = 6` → close date Friday 10/23. BATCH_CLOSE closes it on its first run on or after that date.

`REQUEST_COMPLETE`, `DATA_NOT_PROVIDED` and `COMPLETED_WITH_EXCEPTION` are final ("closed") statuses.

---

## 4. Metadata tables – map and roles

```
                          ┌───────────────────┐
                          │ ComplianceProject │ Project_Cd
                          └─────────┬─────────┘
            ┌───────────────────────┼──────────────────────────────┐
            │                       │                              │
            ▼                       ▼                              ▼
 ┌──────────────────┐  ┌───────────────────────────────┐  ┌───────────────────────┐
 │ ComplianceTable  │  │ ComplianceDataSetSourceXwalk  │  │ComplianceBatchCalendar│
 │ (table groups,   │  │ Project+Table+Src, Run_Ty list│  │ Project+Table+Run_Ty  │
 │  optional)       │  │ + effective dates + version   │  │ + dates (optional)    │
 └──────────────────┘  └───┬──────────────┬────────┬───┘  └───────────────────────┘
                           │ Src_ID       │ Run_Ty │
           ┌───────────────┘              │        └────────────────────┐
           ▼                              ▼                             │
 ┌───────────────────────┐   ┌───────────────────┐                      │
 │ComplianceSourceSystem │   │ ComplianceRunType │  schedule, SLA,      │
 │ Src_ID, name, type    │   │ Run_Ty            │  category            │
 └───────────────────────┘   └─────────┬─────────┘                      │
                                       │ Project + Table (or *) + Run_Ty│
                                       ▼                                │
                             ┌─────────────────────────┐                │
                             │ ComplianceRunTypePeriod │ report dates   │
                             │ default period per      │ per project    │
                             │ project / table         │                │
                             └─────────────────────────┘                │
                                                                        │
 Per Project + Table + Src_ID (how the data arrives), one of:           │
 ┌───────────────────────────┐   ┌───────────────────────────────┐      │
 │ComplianceSourceFileConfig │   │ComplianceInternalSourceConfig │      │
 │ file name, folder, stage  │   │ query, connection, stage      │      │
 │ and core tables           │   │ and core tables               │      │
 └─────────────┬─────────────┘   └───────────────────────────────┘      │
               │ Project+Table+Src                                      │
               ▼                                                        │
 ┌───────────────────────────┐   ┌──────────────────────────────┐       │
 │ ComplianceFileCheckConfig │   │ ComplianceTargetColumnConfig │       │
 │ checks (rows add up)      │   │ derived columns per table    │       │
 └───────────────────────────┘   └──────────────────────────────┘       │
 ┌───────────────────────────┐   ┌──────────────────────────────┐       │
 │ ComplianceExportConfig    │   │ ComplianceRuleBinding        │       │
 │ export table per table    │   │ GRE rules per table/src/run  │       │
 └───────────────────────────┘   └──────────────────────────────┘       │
                                                                        │
 ───────────────────────── created by the framework ────────────────────┘
                                   │
 ┌─────────────────────────┐       ▼
 │ComplianceRequestInTake  │──► ┌──────────────────────────┐ ◄── ComplianceBatchOverride
 │ ad-hoc requests (you)   │    │ ComplianceRequestControl │     REUSE / LATE_ARRIVAL /
 └─────────────────────────┘    │ one row per batch+source │     CORRECTION / REOPEN (you)
                                │ Req_ID, Btch_ID, status  │
                                └──────┬────────────┬──────┘
                                       │ Req_ID     │ Req_ID
                                       ▼            ▼
                     ┌────────────────────┐  ┌──────────────────────────────┐
                     │ ComplianceFileLoad │  │ ComplianceRequestFileDetail  │
                     │ one row per load / │  │ timeline: every event of a   │
                     │ pull (Load_ID)     │  │ batch row                    │
                     └────────────────────┘  └──────────────────────────────┘
                     ┌──────────────────────────────┐  ┌────────────────┐
                     │ CMS_ComplianceExceptionsAudit│  │ ComplianceLock │
                     │ problems and approvals,      │  │ running work   │
                     │ emailed by NOTIFY            │  │ (technical)    │
                     └──────────────────────────────┘  └────────────────┘
```

| Table | Role | Who writes | When |
|---|---|---|---|
| `ComplianceProject` | Projects | You | Once per project |
| `ComplianceTable` | Table groups (optional) | You | When tables are grouped |
| `ComplianceSourceSystem` | Sources | You | Once per source |
| `ComplianceRunType` | Run types: schedule, SLA, category | You | Once per run type |
| `ComplianceRunTypePeriod` | Report dates of each run type | You | Per project (and table, when different) |
| `ComplianceDataSetSourceXwalk` | Who sends what, for which run types, from when | You | Per table and source |
| `ComplianceBatchCalendar` | Extra batches on given dates (optional) | You | For irregular dates |
| `ComplianceSourceFileConfig` | How a source's file looks and where it loads | You | Per file source |
| `ComplianceInternalSourceConfig` | How an internal source is pulled (optional) | You | Per query source |
| `ComplianceFileCheckConfig` | File checks | You | As needed |
| `ComplianceTargetColumnConfig` | Derived columns | You | Per project / table |
| `ComplianceExportConfig` | Export table (optional) | You | Per table |
| `ComplianceRuleBinding` | Data quality rules (optional) | You | Per table / source / run type |
| `ComplianceRequestInTake` | Ad-hoc requests | You | Per request |
| `ComplianceBatchOverride` | Overrides | You (approver) | Per exception |
| `ComplianceRequestControl` | Batches | Framework | Every run |
| `ComplianceFileLoad` | Loads and pulls | Framework | Every file / pull |
| `ComplianceRequestFileDetail` | Batch timeline | Framework | Every event |
| `CMS_ComplianceExceptionsAudit` | Exceptions and approvals | Framework | Every problem / approval |
| `ComplianceLock` | Locks | Framework | While work runs |

---

## 5. Configuration tables (you fill these)

### 5.1 ComplianceProject

One row per project.

| Column | Required | Values | Meaning / where it comes from |
|---|---|---|---|
| `Project_Cd` | Yes | Up to 30 characters, letters / digits / `_` | Your project code. Appears in batch IDs and every other table |
| `Project_Desc` | Yes | Text | Description |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = the project is switched off |

```sql
INSERT INTO HSABC_DEV.ComplianceProject (Project_Cd, Project_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CMS Universe submissions', 1);
```

### 5.2 ComplianceTable (optional)

Puts tables into groups, so a step can run for a group (e.g. all ODAG tables). Without rows here every step works
per project or per table.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Yes | Up to 63 characters | The table (dataset) code used everywhere else |
| `Table_Grp_Cd` | Yes | Up to 30 characters | The group, e.g. `ODAG`, `PDE` |
| `Table_Desc` | No | Text | |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = left out of its group |

```sql
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'ODAG', 'ODAG table 3', 1);
```

### 5.3 ComplianceSourceSystem

One row per source, shared by all projects.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Src_ID` | Yes | Up to 30 characters | The source code. Appears in batch rows, file configs, core rows (source column) |
| `Src_Nm` | Yes | Text | Name shown in reports, e.g. `MedHOK` |
| `Src_Ty` | Yes | Free text, e.g. `FDR`, `INTERNAL` | Information only |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = files of this source are rejected (`SOURCE` finding) |

```sql
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind) VALUES ('51', 'MedHOK', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind) VALUES ('30', 'UMAuth', 'INTERNAL', 1);
```

### 5.4 ComplianceRunType

One row per run type, shared by all projects. Decides **when** batches are created (schedule), their **SLA**
(close date) and whether they are scheduled or ad hoc.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Run_Ty` | Yes | Letters and digits only, up to 20 | The run type code. Appears in file names (`{RUNTY}`) and batch IDs |
| `Run_Ty_Desc` | Yes | Text | |
| `Run_Category_Cd` | Yes | `SCHEDULED`, `ADHOC` | `SCHEDULED`: batches from the schedule; export (and its data) from the close date. `ADHOC`: batches from intake requests (and from a schedule if one is set); pulled and exported only when released |
| `SLA_Days` | Yes | Whole number ≥ 1 | Close date = request date + `SLA_Days` − 1. `1` = closes the same day |
| `Carry_Fwd_Ind` | Default 0 | `1`, `0` | `1` = REUSE (carry-forward) overrides are allowed for this run type |
| `Sched_Cd` | No | `DAILY`, `WEEKLY`, `MONTHLY` | When batches are created (section 8.2). Empty = no code schedule |
| `Sched_Day_Cd` | With `WEEKLY` / `MONTHLY` | `MON`…`SUN`; `1`…`31`; `LAST` | WEEKLY: the weekday. MONTHLY: a day number (a day past month end = last day), `LAST`, or a weekday (with `Sched_Week_Cd`) |
| `Sched_Week_Cd` | MONTHLY on a weekday | `1`, `2`, `3`, `4`, `LAST` | The n-th weekday of the month |
| `Sched_Months_Txt` | No | e.g. `1,4,7,10` | MONTHLY only in these months |
| `Sched_Window_Days` | Default 1 | ≥ 1 | A missed run still creates the batch up to this many days from its scheduled date (same report dates) |
| `Sched_Offset_Days` | Default 0 | −31 … 31 | Moves the date the codes name: `-2` with the 3rd Tuesday = the Sunday before it. Report dates count from the moved date |
| `Batch_Schedule_Sql_Txt` | No | One `SELECT` | Fallback schedule when `Sched_Cd` is empty: returns one row on a due day, none otherwise (section 8.3) |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = no batches for this run type |

```sql
-- Daily, closes the same day
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Window_Days, Active_Ind)
VALUES ('DAILY', 'Daily monitoring for the previous day', 'SCHEDULED', 1, 0, 'DAILY', 1, 1);
-- Weekly, created Sunday, closes Friday
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Window_Days, Active_Ind)
VALUES ('WEEKLY', 'Weekly, previous Monday to Sunday', 'SCHEDULED', 6, 0, 'WEEKLY', 'SUN', 2, 1);
-- Monthly on the 3rd Tuesday, 5 days to catch up a missed run
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Window_Days, Active_Ind)
VALUES ('MNT', 'Monthly submission', 'SCHEDULED', 10, 0, 'MONTHLY', 'TUE', '3', 5, 1);
-- Monthly on the Sunday before the 3rd Tuesday
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Offset_Days, Sched_Window_Days, Active_Ind)
VALUES ('MNT2', 'Monthly, Sunday before the 3rd Tuesday', 'SCHEDULED', 5, 0, 'MONTHLY', 'TUE', '3', -2, 1, 1);
-- Quarterly on the 1st of Jan / Apr / Jul / Oct
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Months_Txt, Sched_Window_Days, Active_Ind)
VALUES ('QTR', 'Quarterly', 'SCHEDULED', 15, 0, 'MONTHLY', '1', '1,4,7,10', 3, 1);
-- Ad hoc (requested through ComplianceRequestInTake), carry-forward allowed
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Active_Ind)
VALUES ('CMS', 'CMS ad-hoc request', 'ADHOC', 5, 1, 1);
```

### 5.5 ComplianceRunTypePeriod (report dates per run type)

The report dates of each scheduled run type, set once per project instead of on every crosswalk row. A row for
one table wins over the project's `*` row; `Rpt_Period_Cd` on a crosswalk row wins over both.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Default `*` | A table or `*` | `*` = every table of the project |
| `Run_Ty` | Yes | A run type | |
| `Rpt_Period_Cd` | Yes | Period code (section 8.4) | Report dates, counted from the batch's scheduled date |
| `Active_Ind` | Default 1 | `1`, `0` | |

Ad-hoc run types need no row (their dates come from the request). Careful: an ad-hoc run type that has a schedule
creates scheduled batches for **every** source that finds a period; when only some sources are scheduled, give it
no row here and put `Rpt_Period_Cd` on those sources' crosswalk rows.

```sql
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'DAILY', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'WEEKLY', 'PREV_CALENDAR_WEEK', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'MNT', 'PREV_CALENDAR_MONTH', 1);
-- WEEKLY2: the Monday-Sunday week 5 weeks back
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'WEEKLY2', 'RANGE(WEEK,5,1)', 1);
-- ODAG5 reports the quarter for MNT instead of the month
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', 'ODAG5', 'MNT', 'PREV_CALENDAR_QUARTER', 1);
```

### 5.6 ComplianceDataSetSourceXwalk (crosswalk)

The heart of the setup: **one row per table + source**, listing the run types the source gets batches for, with
the period the row is valid for and the compliance version.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | Existing codes | This source sends this table |
| `Run_Ty` | Yes | One run type, or a list `DAILY,WEEKLY,MNT` (up to 200 characters) | The run types it sends. Only the listed ones: a run type added later applies to no source until it is listed |
| `Effective_Start_Dt_Key` | Yes | Date | First run date the row applies |
| `Effective_End_Dt_Key` | No | Date | Last run date (empty = open ended). End a row and add a new one to change the version |
| `Cmplnc_Vrsn` | Yes | Up to 10, e.g. `2200` | The compliance version; stored on each batch, written to the `VERSION` column |
| `Rpt_Period_Cd` | No | Period code (section 8.4) | Only when this row's dates differ from `ComplianceRunTypePeriod` |
| `Rpt_Dt_Sql_Txt` | No | One `SELECT` | Fallback for dates no code expresses (section 8.3) |
| `Include_Ind` | Default 1 | `1`, `0` | On a row for one run type: `0` switches that run type off for the source |
| `Active_Ind` | Default 1 | `1`, `0` | |

**Exceptions for one run type.** A row with a single run type, for a run type that is also in the source's list,
**replaces** the list's entry for that run type while the single row is effective:

- different version or dates for one run type: a row with its own `Cmplnc_Vrsn` / `Effective_*` / `Rpt_Period_Cd`;
- the source does not send that run type: a row with `Include_Ind = 0`.

The report dates of a batch come from the first that has them: the crosswalk row's `Rpt_Period_Cd`, its
`Rpt_Dt_Sql_Txt`, the table's `ComplianceRunTypePeriod` row, the project's `*` row. Old style rows (one per run type,
each with its own `Rpt_Period_Cd`) keep working.

```sql
-- internal source 10: daily, monthly and ad hoc
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '10', 'DAILY,MNT,CMS', DATE '2026-01-01', NULL, '2200', 1, 1);
-- FDR 51: weekly, monthly and ad hoc
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'WEEKLY,MNT,CMS', DATE '2026-01-01', NULL, '2200', 1, 1);
-- ... but 51's MNT moves to version 2300 from November, with the last 3 months as report dates
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Period_Cd, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'MNT', DATE '2026-11-01', NULL, '2300', 'RANGE(MONTH,3,3)', 1, 1);
-- ESI 140 sends weekly, monthly and ad hoc ...
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'WEEKLY,MNT,CMS', DATE '2026-01-01', NULL, '2200', 1, 1);
-- ... but stops its weekly batches from December (same as removing WEEKLY from the list, with a start date)
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'WEEKLY', DATE '2026-12-01', NULL, '2200', 0, 1);
```

### 5.6a ComplianceBatchCalendar (optional)

Batches on given dates with given report dates, next to the schedule: an extra or moved submission.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd` | Yes | | |
| `Table_Nm` | Default `*` | A table or `*` | `*` = every table of the run type |
| `Run_Ty` | Yes | Any active run type | |
| `Req_Dt_Key` | Yes | Date | The day BATCH_CREATION creates the batch (missed day: run with `as_of`) |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Yes | Dates | Report dates |
| `Active_Ind` | Default 1 | | |

```sql
INSERT INTO HSABC_DEV.ComplianceBatchCalendar (Project_Cd, Table_Nm, Run_Ty, Req_Dt_Key, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)
VALUES ('CMSUNIV', '*', 'MNT', DATE '2026-11-03', DATE '2026-08-01', DATE '2026-08-31');
```

### 5.7 ComplianceSourceFileConfig (file sources)

One row per source that **sends files**, per table.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Cfg_ID` | (framework) | Number | Generated: leave out |
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | | |
| `Src_File_Nm_Tmplt` | Yes | Fixed text + tokens (section 8.5) | How the file is named; the name tells run type and report dates |
| `Delmtr_Cd` | No | One character: `\|` `,` `;` `*` …, `\t` = tab | Field delimiter; empty = comma. Not words like `PIPE` |
| `Src_File_Has_Hdr_Ind` | Yes | `1`, `0` | First line is a header (skipped when loading) |
| `Src_File_Has_Trlr_Ind` | Yes | `1`, `0` | Last line is a trailer (checked with a `TRAILER` check, not loaded) |
| `Allow_Zero_Rcd_Ind` | Yes | `0` or `1` | No longer used (a file with no rows is the source's "no data"); put `0` |
| `Src_File_Path` | Yes | `\\server\share\folder`, `//server/share/folder`, `smb://…`, `nas://share/folder`; `$env` allowed | Inbound NAS folder. `Archive`, `Error`, `ErrorLog`, `ControlReport` subfolders are created in it |
| `Stg_Schema_Nm`, `Stg_Table_Nm` | Yes | Database and table; `$env` allowed in the database | Staging table |
| `Core_Schema_Nm`, `Core_Table_Nm` | Yes | Database and table | Core table |
| `File_Vrsn_Cd` | With `{VERSION}` | e.g. `22` | The version as written in file names |
| `Active_Ind` | Default 1 | | |

```sql
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind,
   Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'CMSAuth_MedHOK_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0,
        '\\nas01\Compliance\$env\cmsuniv\FDR\PYMT_C', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022',
        'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
```

**Staging and core tables** are yours (DDL by your DBA). They need: the file's columns; `Btch_ID VARCHAR(250)`;
a **source column** (e.g. `Src_Data_Key`) mapped to `SOURCE_ID` in `ComplianceTargetColumnConfig`; and the derived
columns you map. A run-type column (mapped to `RUN_TYPE`) lets staging be cleared per run type.

### 5.8 ComplianceInternalSourceConfig (internal sources, optional)

One row per internal source that is **pulled with a query** (SOURCE_LOAD) instead of sending a file. Such a source
has no file config.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | | |
| `Src_Db_Ty` | Yes | `TERADATA`, `SQLSERVER` | Where the source table is |
| `Src_Conn_Cd` | No | An Airflow Connection id; `$env` allowed | Empty = the framework's own Teradata system (copied with one INSERT … SELECT); otherwise the source server's Connection |
| `Src_Sql_Txt` | Yes | One `SELECT` in the source's dialect | Its column names fill the staging columns of the same name. Placeholders (section 8.9) are bound as parameters. `$env` is **not** replaced inside it |
| `Stg_Schema_Nm` … `Core_Table_Nm` | Yes | | As in the file config |
| `Active_Ind` | Default 1 | | |

```sql
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig
  (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '30', 'TERADATA', NULL,
        'SELECT a.Mbr_First_Nm AS MemberFirstName, a.Mbr_ID AS MemberID FROM CMSUNIV_SRC_DEV_V.UM_AUTH a WHERE a.Btch_ID = {btch_id} AND a.Src_ID = {src_id}',
        'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig
  (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '40', 'SQLSERVER', 'cms_src_appeals_sqlsrv',
        'SELECT m.FirstName AS MemberFirstName, m.MemberNumber AS MemberID FROM dbo.Appeal a JOIN dbo.Member m ON m.MemberKey = a.MemberKey WHERE a.ReceivedDate BETWEEN {rpt_start} AND {rpt_end}',
        'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', 1);
```

When is it pulled: scheduled run types from the request date on (an empty result before the close date means "no
data yet" and is tried again on the next runs; empty on or after the close date means "no data"); ad hoc only when
released (section 11.6).

### 5.9 ComplianceFileCheckConfig (file checks)

Which checks a file gets. **Rows add up**: every check set for the project (`*`), the table or the source applies.
For the **same check** (same `Check_Cd` and `Check_Field_Nm`) the most specific row wins: table + source, then table,
then source, then `*`. `Include_Ind = 0` switches an inherited check off.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd` | Yes | | |
| `Table_Nm` | Default `*` | A table or `*` | |
| `Src_ID` | Default `*` | A source or `*` | |
| `Check_Cd` | Yes | Section 8.6 | The check |
| `Check_Field_Nm` | Default `*` | A field name or `*` | For field checks: the field (a `HEADER_NAMES` name, else a staging column). `*` for the other checks |
| `Check_Val_Txt` | Yes when included | Depends on the check | The check's value |
| `Include_Ind` | Default 1 | `1`, `0` | `1` = applies; `0` = switched off for the files the row covers |
| `Active_Ind` | Default 1 | | |

```sql
-- every ODAG3 file: header names and value checks
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '*', 'HEADER_NAMES', '*', 'MemberFirstName|MemberID|Amount', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', '*', '*', 'COLUMN_VALUES', '*', 'ALL', 1, 1);
-- source 140 has its own header, and MemberID must be at least 9 characters
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'HEADER_NAMES', '*', 'H|First_Nm|Mbr_ID|Amt', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'MIN_LENGTH', 'MemberID', '9', 1, 1);
-- source 20 has no record types: switch the inherited check off
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '20', 'RECORD_TYPE', '*', NULL, 0, 1);
```

### 5.10 ComplianceTargetColumnConfig (derived columns)

Which staging / core columns the framework fills, and with what. **Every other staging column is a file field**, in
table order (or as `TARGET_COLUMNS` / `HEADER_NAMES` say).

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd` | Yes | | |
| `Table_Nm` | Default `*` | A table or `*` | A table row wins over the `*` row for the same column |
| `Column_Nm` | Yes | A staging / core column | |
| `Value_Cd` | Yes | Section 8.7 | What fills it |
| `Value_Txt` | For `CONSTANT`, `SQL` | The value or one `SELECT` | |
| `Active_Ind` | Default 1 | | |

`Btch_ID` needs no row (it is always the batch ID). When the sources of a table have staging tables with different
column names (e.g. `Src_Data_Key` for files, `Source_ID` for an internal source), add a row for each name: a mapped
column a source's tables do not have is skipped for that source. A column mapped to `SOURCE_ID` is **required** in every staging,
core and export table.

```sql
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'Src_Data_Key', 'SOURCE_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'Run_Ty', 'RUN_TYPE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'Universe_Vrsn', 'VERSION', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'FileName', 'FILE_NAME', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'StagingCreateDate', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'Load_Dtts', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', 'ODAG3', 'ODAG_DE_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
-- a value looked up from another table (one row, one column); per batch row: use {src_id} or {req_id}
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Audit_Id', 'SQL', 'SELECT Req_ID + 1000 FROM HSABC_DEV.ComplianceRequestControl WHERE Req_ID = {req_id}', 1);
```

### 5.11 ComplianceExportConfig (optional)

The export table of a core table (EXPORT_LOAD). One row per project + table.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd`, `Table_Nm` | Yes | | |
| `Exp_Schema_Nm`, `Exp_Table_Nm` | Yes | Database and table; `$env` allowed | The export table: the core table's columns (row ID included), `Btch_ID`, the source column, optionally `Exp_Btch_ID` |
| `Exp_Btch_Col_Nm` | No | Column name | Column that gets the batch ID again (default `Exp_Btch_ID`, filled only if present) |
| `Exp_Proc_Nm` | No | `database.procedure`; `$env` allowed | A procedure that does the export instead of the framework (it deletes and inserts that source's rows itself) |
| `Exp_Proc_Parm_Txt` | No | e.g. `{btch_id}, {data_btch_id}, {src_id}` | The procedure's parameters in order (section 8.10); empty = `{btch_id}, {data_btch_id}, {exp_btch_id}` |
| `Active_Ind` | Default 1 | | |

```sql
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Exp_Proc_Nm, Exp_Proc_Parm_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'CMSUNIV_EXPORT_$ENV_T', 'ODAG_DE_TABLE3_2022_EXP', NULL, NULL, NULL, 1);
-- the export table (DBA):
-- CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG_DE_TABLE3_2022_EXP AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE3_2022 WITH NO DATA;
-- ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG_DE_TABLE3_2022_EXP ADD Exp_Btch_ID VARCHAR(100);
```

What the export holds: per source, run type and report period, the latest batch with data. A carried-forward batch
gets a copy of the reused rows under its own batch ID.

### 5.12 ComplianceRuleBinding (optional)

Which GRE rule group runs on a loaded file (FILE_RULES). Rows add up.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd` | Yes | | |
| `Table_Nm` | Yes | A table or `*` | |
| `Src_ID` | Yes | A source or `*` | |
| `Run_Ty` | Default `*` | A run type or `*` | |
| `Gre_Rule_Group`, `Gre_Rule_Variant` | Yes | As defined in the GRE; variant `*` = all | |
| `Active_Ind` | Default 1 | | |

```sql
INSERT INTO HSABC_DEV.ComplianceRuleBinding (Project_Cd, Table_Nm, Src_ID, Run_Ty, Gre_Rule_Group, Gre_Rule_Variant, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '*', '*', 'CMSUNIV_ODAG3', 'DEFAULT', 1);
```

---

## 6. Request and action tables (you add rows when needed)

### 6.1 ComplianceRequestInTake (ad-hoc requests)

One row per request. BATCH_CREATION creates the batches on each day of the request window.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Intake_ID` | (framework) | | Generated: leave out |
| `Project_Cd`, `Table_Nm` | Yes | | |
| `Src_ID` | No | A source or NULL | NULL = every source with a crosswalk row for that run type |
| `Run_Ty` | Yes | Any active run type (ad hoc, or a scheduled one outside its schedule) | |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Yes | Dates | Report dates of the batch |
| `Req_Start_Dt_Key`, `Req_End_Dt_Key` | Yes | Dates | The days batches are created (same date twice = one batch) |
| `Last_Run_Dt_Key` | (framework) | | Last day the request was handled: leave out |

A request whose source, run type and report dates already have an **open** batch made by the schedule or another
request uses that batch.

```sql
INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
VALUES ('CMSUNIV', 'ODAG3', NULL, 'CMS', DATE '2026-07-01', DATE '2026-09-30', DATE '2026-10-19', DATE '2026-10-19');
-- a daily ad-hoc batch for the previous day, for every day of November (one row per day)
INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
SELECT 'CMSUNIV', 'ODAG3', '140', 'CMS', c.calendar_date - 1, c.calendar_date - 1, c.calendar_date, c.calendar_date
FROM sys_calendar.calendar c WHERE c.calendar_date BETWEEN DATE '2026-11-01' AND DATE '2026-11-30';
```

### 6.2 ComplianceBatchOverride (overrides)

One row per exception, for **one source** of a batch (`Req_ID`). Applied only when `Apprvl_Stat = 'APPROVED'` and
`Valid_Thru_Dt_Key` is today or later.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Ovrd_ID` | (framework) | | Generated |
| `Override_Ty` | Yes | `REUSE`, `LATE_ARRIVAL`, `CORRECTION`, `REOPEN` | Section 8.8 |
| `Req_ID`, `Btch_ID` | Yes | From `ComplianceRequestControl` | The batch row (one source) |
| `Reuse_Btch_ID` | REUSE only | A batch ID or NULL | The earlier batch whose data is reused (NULL = the latest earlier closed batch with data) |
| `Apprvl_Stat` | Default `PENDING_REVIEW` | `PENDING_REVIEW`, `APPROVED`, `REJECTED` | |
| `Valid_Thru_Dt_Key` | Yes to apply | Date | Last day the override may be used |
| `Rsn_Txt` | Recommended | Text | Why |
| `Reviewed_By`, `Reviewed_Dtts` | When approved | Name, timestamp | The approver |

```sql
-- REUSE: the open batch of source 51 reuses the latest earlier batch (apply BEFORE the batch's close run)
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Reuse_Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'REUSE', Req_ID, Btch_ID, NULL, 'APPROVED', DATE '2026-10-31', 'MedHOK confirmed no change', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DAILY' AND Src_ID = '51';
-- LATE_ARRIVAL: a closed batch without data accepts a late file
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'LATE_ARRIVAL', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31', 'ESI file came late', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DAILY' AND Src_ID = '140';
-- CORRECTION: a closed batch with data accepts a corrected file
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'CORRECTION', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31', 'Corrected amounts', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DAILY' AND Src_ID = '51';
-- REOPEN: open a closed batch again until new data arrives
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'REOPEN', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31', 'Resend expected', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DAILY' AND Src_ID = '140';
```

Approve, reject or stop a row:

```sql
UPDATE HSABC_DEV.ComplianceBatchOverride SET Apprvl_Stat = 'APPROVED', Reviewed_By = 'approver_id', Reviewed_Dtts = CURRENT_TIMESTAMP(6), Valid_Thru_Dt_Key = DATE '2026-10-31'
WHERE Ovrd_ID = 123 AND Apprvl_Stat = 'PENDING_REVIEW';
UPDATE HSABC_DEV.ComplianceBatchOverride SET Apprvl_Stat = 'REJECTED', Reviewed_By = 'approver_id', Reviewed_Dtts = CURRENT_TIMESTAMP(6)
WHERE Ovrd_ID = 123 AND Apprvl_Stat = 'PENDING_REVIEW';
UPDATE HSABC_DEV.ComplianceBatchOverride SET Valid_Thru_Dt_Key = CURRENT_DATE - 1 WHERE Ovrd_ID = 123 AND Apprvl_Stat = 'APPROVED';
```

---

## 7. Tables the framework writes (read only)

### 7.1 ComplianceRequestControl – batches

One row per batch **and source**.

| Column | Meaning |
|---|---|
| `Req_ID` | The batch row's number; used by overrides, loads and the timeline |
| `Project_Cd`, `Table_Nm`, `Src_ID`, `Run_Ty` | What the batch row is for |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Report dates |
| `Req_Dt_Key` | Request date (the day it was created); close date = `Req_Dt_Key + SLA_Days − 1` |
| `Btch_ID` | `{YYYYMMDD}_{Project}_{Table}_{Run_Ty}`, shared by every source; `_2`, `_3` for another report period on the same day |
| `Cmplnc_Vrsn` | Compliance version from the crosswalk |
| `Req_Stat` | Status (section 8.11) |
| `Resolution_Ty` | `NEW_FILE`, `NO_DATA`, `CARRY_FORWARD`, `MISSING` (section 8.12) |
| `Reuse_Btch_ID` | For a carry-forward: the batch whose data is reused |
| `Batch_Close_Ind` | `0` open, `1` closed |

### 7.2 ComplianceFileLoad – loads and pulls

One row per file pickup or internal-source pull (`Load_ID`). `Load_Stat` and `Rules_Stat`: section 8.13.
`Quarantine_Rsn_Cd` holds the rejection reason (an audit event code), `Error_Txt` the technical detail,
`Stg_Rcd_Cnt` the rows staged. Pulls have `File_Share = QUERY:<connection or LOCAL>`.

### 7.3 ComplianceRequestFileDetail – the batch timeline

One row per event of a batch row (`Req_ID`): `Event_Ty` (section 8.14), `Actor`, `Event_Txt`, `Event_Dtts`
(Central time), and the `Load_ID` / `Ovrd_ID` / `Intake_ID` involved.

### 7.4 CMS_ComplianceExceptionsAudit – exceptions and approvals

One row per problem or approval: `Event_Ty` (section 8.15), `Sevrty` (`ERROR`, `WARNING`, `INFO`), the project /
table / source / run type / batch / load / override involved, `Event_Txt`, and `Notified_Ind` (`0` = not emailed
yet, `1` = emailed or not to be emailed). NOTIFY emails every row with `Notified_Ind = 0`.

### 7.5 ComplianceLock – running work

One row per lock held by a running step (a batch row, a staging table, an export table …). A crashed run leaves its
locks until they expire (`LOCK_TTL_MINUTES`, default 240) or are released (section 11.15).

---

## 8. Reference lists (all codes and values)

### 8.1 Run categories (`ComplianceRunType.Run_Category_Cd`)

| Value | Batches created | Internal sources pulled | Exported |
|---|---|---|---|
| `SCHEDULED` | By the schedule (codes or SQL), the calendar, or an intake request | From the request date (empty before the close date = try again) | From the close date (from `RELEASE_TIME` on that day, if set), or when released |
| `ADHOC` | By intake requests (and by a schedule if the run type has one, for crosswalk rows that find a report period) | When released | When released |

### 8.2 Schedule codes (`ComplianceRunType`)

| `Sched_Cd` | `Sched_Day_Cd` | `Sched_Week_Cd` | `Sched_Months_Txt` | Due on |
|---|---|---|---|---|
| `DAILY` | | | | Every day |
| `WEEKLY` | `MON` … `SUN` | | | That weekday |
| `MONTHLY` | `1` … `31` | | optional | That day (a day past month end = last day) |
| `MONTHLY` | `LAST` | | optional | The last day of the month |
| `MONTHLY` | `MON` … `SUN` | `1` … `4`, `LAST` | optional | The n-th (or last) weekday of the month |

`Sched_Offset_Days` (−31 … 31, default 0) moves the date: e.g. `MONTHLY`, `TUE`, `3`, `-2` = the Sunday before the
3rd Tuesday.

`Sched_Window_Days` (≥ 1): the batch can still be created up to that many days from its scheduled date (missed
run). One batch per scheduled date, never one per day of the window. Every scheduled date gets its batch, also when
its report dates are the same as the previous one's (a DAILY run type with fixed dates, e.g. `ANNUAL_WINDOW`, gets a
batch every day).

### 8.3 SQL fallbacks for schedule and report dates

Only when no code is set. Both are a single `SELECT` (no `;` inside).

- `Batch_Schedule_Sql_Txt`: returns **one row** on a day batches are due, **no row** otherwise. Placeholders:
  `{run_date}`, `{project_cd}`, `{run_ty}`. Its columns can be used by name in `Rpt_Dt_Sql_Txt` (e.g. `period_dt`).
- `Rpt_Dt_Sql_Txt`: returns one row with `rpt_start` and `rpt_end`. Placeholders: `{run_date}`, `{project_cd}`,
  `{run_ty}`, `{table_nm}`, `{src_id}`, the schedule row's columns (with a code schedule: `{period_dt}` = the
  scheduled date).

```sql
-- every Monday
SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE TD_DAY_OF_WEEK(d) = 2
-- previous day
SELECT CAST({run_date} AS DATE) - 1 AS rpt_start, CAST({run_date} AS DATE) - 1 AS rpt_end
```

### 8.4 Report period codes (`ComplianceRunTypePeriod.Rpt_Period_Cd`, `ComplianceDataSetSourceXwalk.Rpt_Period_Cd`)

Counted from the batch's scheduled date (D).

| Code | Report dates |
|---|---|
| `SAME_DAY` | D to D |
| `PREV_DAY` | D−1 to D−1 |
| `PREV_N_DAYS(n)` | D−n to D−1 |
| `PREV_WEEK_SAME_DAY(n)` | D − 7·n to D−1 |
| `PREV_CALENDAR_WEEK` | Monday to Sunday of the week before D |
| `CURRENT_CALENDAR_MONTH` | First to last day of D's month |
| `PREV_CALENDAR_MONTH` | First to last day of the month before D |
| `ROLLING_1_MONTH` | Same day one month before D to D−1 |
| `PREV_CALENDAR_QUARTER` | The quarter before D's quarter |
| `PREV_CALENDAR_YEAR` | 1 Jan to 31 Dec of the year before D |
| `ANNUAL_WINDOW(MM-DD,MM-DD[,MM-DD])` | The same dates every year (e.g. `12-01,01-31`); the optional third date is when it moves to the next window |
| `RANGE(unit,back,length)` | Any other range: `length` units, starting `back` units before D's unit. Units: `DAY`, `WEEK` (Monday–Sunday), `MONTH`, `QUARTER`, `YEAR` |
| `FIRST_DAYS(unit,back,days)` | The first `days` days of the unit `back` units before D's unit (`WEEK`, `MONTH`, `QUARTER`, `YEAR`; at most 7 / 28 / 90 / 365) |
| `LAST_DAYS(unit,back,days)` | The last `days` days of that unit, e.g. `LAST_DAYS(MONTH,1,14)` = the last 14 days of last month |

`RANGE` examples, for D = Sunday 2026-10-18:

| Code | Report dates | In words |
|---|---|---|
| `RANGE(DAY,1,1)` | 10-17 | the day before |
| `RANGE(DAY,7,7)` | 10-11 to 10-17 | the last 7 days |
| `RANGE(DAY,35,7)` | 09-13 to 09-19 | the same 7 days, 4 weeks earlier |
| `RANGE(WEEK,0,1)` | 10-12 to 10-18 | this week |
| `RANGE(WEEK,1,1)` | 10-05 to 10-11 | last week |
| `RANGE(WEEK,5,1)` | 09-07 to 09-13 | the week 5 weeks back |
| `RANGE(WEEK,2,2)` | 09-28 to 10-11 | the last 2 full weeks |
| `RANGE(MONTH,0,1)` | 10-01 to 10-31 | this month |
| `RANGE(MONTH,1,1)` | 09-01 to 09-30 | last month |
| `RANGE(MONTH,2,1)` | 08-01 to 08-31 | the month before last |
| `RANGE(MONTH,3,3)` | 07-01 to 09-30 | the last 3 full months |
| `RANGE(QUARTER,1,1)` | 07-01 to 09-30 | last quarter |
| `RANGE(YEAR,1,1)` | 2025-01-01 to 2025-12-31 | last year |
| `LAST_DAYS(MONTH,1,14)` | 09-17 to 09-30 | the last 14 days of last month |
| `FIRST_DAYS(MONTH,0,10)` | 10-01 to 10-10 | the first 10 days of this month |

Fixed dates that repeat every year: `ANNUAL_WINDOW`. One-off or irregular dates: `ComplianceBatchCalendar`.

### 8.5 File name tokens (`Src_File_Nm_Tmplt`)

| Token | Meaning |
|---|---|
| `{RUNTY}` | The run type (required, once) |
| `{RPTSTART}`, `{RPTEND}` | Report dates, `YYYYMMDD` unless a format is given (required, once each) |
| `{RPTMONTH}` | Instead of the two dates: a month (`YYYYMM`) = first to last day |
| `{TS}` | File timestamp, 12 or 14 digits unless a format is given (optional) |
| `{VERSION}` | Must equal `File_Vrsn_Cd` |
| `{PROJECT}`, `{TABLE}`, `{SRC}` | The row's project, table, source codes |
| `{ANY}` | Any text, ignored |

Formats: `{RPTSTART:MMDDYYYY}`, `{RPTEND:YYYY-MM-DD}`, `{RPTMONTH:MMYYYY}`, `{TS:YYYYMMDD}` (built from `YYYY YY MM DD
HH MI SS` and separators). Two tokens read from the name need fixed text between them; the template ends with the
extension (`.txt`, `.csv`).

Example: `CMSAuth_MedHOK_PYMT_C_22_MNT_20260901_20260930_20261019093000.txt` matches
`CMSAuth_MedHOK_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt` with `File_Vrsn_Cd = 22`.

### 8.6 File check codes (`ComplianceFileCheckConfig.Check_Cd`)

| `Check_Cd` | Field | `Check_Val_Txt` example | What is checked |
|---|---|---|---|
| `RECORD_TYPE` | `*` | `H,D,T` | First field of header, data rows and trailer |
| `HEADER_NAMES` | `*` | `MemberFirstName\|MemberID\|Amount` | The header line's names in order (case and spaces ignored); also decides which column each field loads to |
| `COLUMN_COUNT` | `*` | `23` | Fields per header / data row (record type field included) |
| `TARGET_COLUMNS` | `*` | `MemberFirstName\|MemberID` | Not a check: the staging column of each field, in file order |
| `TRAILER` | `*` | `RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP` | The trailer's fields: `RECORD_TYPE`, `FILE_NAME`, `DATA_ROW_COUNT`, `TOTAL_ROW_COUNT`, `TIMESTAMP`, `SKIP` |
| `CHARACTER_SET` | `*` | `latin-1` | Only characters of that set, no control characters |
| `COLUMN_VALUES` | `*` | `ALL` or `LENGTH,NUMBER,REQUIRED` | Each value fits its staging column (length / number / NOT NULL) |
| `MIN_RECORD_LENGTH`, `MAX_RECORD_LENGTH` | `*` | `250` | Characters per data line |
| `MIN_ROWS`, `MAX_ROWS` | `*` | `100` | Data rows in the file |
| `MIN_LENGTH`, `MAX_LENGTH` | field | `9` | Characters of the field's value |
| `MIN_VALUE`, `MAX_VALUE` | field | `0` | The field's value is a number not below / above |
| `VALUE_IN` | field | `Y\|N` | One of the listed values |
| `PATTERN` | field | `[A-Z][0-9]{8}` | The whole value matches the regular expression |
| `DATE_FORMAT` | field | `YYYYMMDD`, `MM/DD/YYYY` | A valid date / timestamp in that format |

Always checked, no row needed: file name, batch, source, encoding, blank lines, structure. Field checks skip empty
values (`COLUMN_VALUES REQUIRED` covers those).

### 8.7 Value codes (`ComplianceTargetColumnConfig.Value_Cd`)

| `Value_Cd` | Filled with |
|---|---|
| `BATCH_ID` | The batch ID, in a table whose batch column is not called `Btch_ID` (e.g. `Batch_ID`, `BatchID`): that column is then the table's batch column |
| `SOURCE_ID` | The batch row's `Src_ID` (**required** in every table) |
| `RUN_TYPE` | The run type |
| `VERSION` | The batch's compliance version (`Cmplnc_Vrsn`) |
| `FILE_NAME` | The file name (pulls: project/table/source) |
| `LOAD_TIMESTAMP` | Load time (timestamp, or text `YYYY-MM-DD HH:MM:SS` in a character column) |
| `LOAD_DATE` | Load date (date, or text `YYYY-MM-DD`) |
| `ROW_SEQUENCE` | Running row number, continuing from the highest in staging and core (row ID) |
| `LOAD_ID`, `REQ_ID` | The load number, the batch row number |
| `PROJECT`, `TABLE` | Project and table codes |
| `RPT_START`, `RPT_END` | Report dates |
| `CURRENT_FLAG` | Core only: always 1 |
| `END_TIMESTAMP` | Core only: always empty |
| `CONSTANT` | `Value_Txt` on every row |
| `SQL` | The one value of the `SELECT` in `Value_Txt` (placeholders section 8.9) |

### 8.8 Override types (`ComplianceBatchOverride.Override_Ty`)

| Type | For | Effect | Applied by |
|---|---|---|---|
| `REUSE` | An open batch without data, run type with `Carry_Fwd_Ind = 1` | Carries the data of an earlier closed batch (same table, source, run type) forward; closes as `REQUEST_COMPLETE` / `CARRY_FORWARD`. Must be applied before the batch's close run | OVERRIDE_DECISIONS |
| `LATE_ARRIVAL` | A closed batch without data | A late file is accepted; the batch stays closed, becomes `REQUEST_COMPLETE` | FILE_LOAD |
| `CORRECTION` | A closed batch with data | A corrected file replaces the data; the batch stays closed | FILE_LOAD |
| `REOPEN` | Any closed batch | Opens it again (status of its data); closes again after new data arrives (or `close-batch`). Applied once per row | OVERRIDE_DECISIONS |

`Apprvl_Stat`: `PENDING_REVIEW` (default, not used), `APPROVED` (used while `Valid_Thru_Dt_Key` ≥ today), `REJECTED`.

### 8.9 Placeholders in SQL text

Bound as parameters (never pasted into the text).

| Where | Placeholders |
|---|---|
| `Src_Sql_Txt` (query sources), `Value_Txt` of `SQL` columns | `{btch_id}`, `{src_id}`, `{run_ty}`, `{version}`, `{project_cd}`, `{table_nm}`, `{req_id}`, `{load_id}`, `{rpt_start}`, `{rpt_end}`, `{load_date}`, `{file_name}` |
| `Batch_Schedule_Sql_Txt` | `{run_date}`, `{project_cd}`, `{run_ty}` |
| `Rpt_Dt_Sql_Txt` | `{run_date}`, `{project_cd}`, `{run_ty}`, `{table_nm}`, `{src_id}`, the schedule row's columns |

`{btch_id}` is shared by every source of the batch: add `{src_id}` (or use `{req_id}`) when the lookup is per source.

### 8.10 Export procedure parameters (`Exp_Proc_Parm_Txt`)

`{btch_id}` (the batch exported), `{data_btch_id}` (the batch whose core rows are its data: itself, the batch it
reuses, or NULL when no data), `{exp_btch_id}` (= the batch ID), `{reused_btch_ids}` (batches it reuses, comma
separated: keep their rows), `{project_cd}`, `{table_nm}`, `{src_id}`, `{run_ty}`, `{version}`, `{req_id}`,
`{req_dt}`, `{rpt_start}`, `{rpt_end}`. The procedure deletes and inserts only that source's rows and fills the source
column.

### 8.11 Batch statuses (`ComplianceRequestControl.Req_Stat`)

| Status | Open / closed | Meaning |
|---|---|---|
| `REQUEST_CREATED` | Open | Created; no data yet |
| `SOURCE_DATA_READY` | Open | Data loaded / pulled |
| `SOURCE_DATA_READY_NO_DATA` | Open | The source delivered "no data" (empty file, or empty pull on/after the close date) |
| `SOURCE_DATA_CARRY_FORWARD` | Open | Data carried forward (REUSE) |
| `EXCEPTION_PENDING` | Open | An exception is pending (older rows) |
| `REQUEST_COMPLETE` | Closed | Closed with data (`NEW_FILE` or `CARRY_FORWARD`) |
| `DATA_NOT_PROVIDED` | Closed | Closed without data (`NO_DATA` or `MISSING`). Final, like `REQUEST_COMPLETE` |
| `COMPLETED_WITH_EXCEPTION` | Closed | Closed while an exception was pending |

### 8.12 Resolution types (`Resolution_Ty`)

`NEW_FILE` (own data), `NO_DATA` (the source said no data), `CARRY_FORWARD` (reused data), `MISSING` (nothing by the
close date).

### 8.13 Load and rules statuses (`ComplianceFileLoad`)

| `Load_Stat` | Meaning | Report status |
|---|---|---|
| `RECEIVED`, `STAGING`, `STAGED` | In progress | – |
| `PROMOTED` | Loaded into core: the batch's current data | ACCEPTED |
| `SUPERSEDED` | Was loaded, replaced by a newer load | ACCEPTED |
| `QUARANTINED` | Rejected (`Quarantine_Rsn_Cd` says why); file moved to `Error` | REJECTED |
| `RULES_FAILED` | Rejected by rules (older behaviour) | REJECTED |
| `FAILED_TECHNICAL` | System problem; tried again next run | FAILED |
| `NO_DATA_YET` | Internal source had no rows before the close date; tried again | WAITING |

| `Rules_Stat` | Meaning |
|---|---|
| `NOT_RUN` | Rules not run yet (FILE_RULES waits for the export when the table has one) |
| `PASSED` / `PASSED_WITH_WARNINGS` | All rules passed (warnings under `FILE_RULES_MODE = ANNOTATE`) |
| `FAILED` | A rule failed (reported, emailed; nothing else changes) |
| `SKIPPED` | No rule binding for the file |
| `ERROR` | The engine could not run; tried again |

### 8.14 Timeline events (`ComplianceRequestFileDetail.Event_Ty`)

| Event | When |
|---|---|
| `BATCH_CREATED` | The batch row was created |
| `FILE_RECEIVED` | A file / pull reached the batch |
| `FILE_PROMOTED` | Data loaded into core |
| `LATE_ARRIVAL_PROMOTED`, `CORRECTION_PROMOTED` | Data loaded into a closed batch with an override |
| `FILE_REPLACED_BEFORE_CLOSE` | A new file replaced the batch's data (or its carry-forward) before close |
| `SOURCE_NO_DATA_YET` | Internal source pull had no rows before the close date |
| `CARRY_FORWARD_APPLIED`, `CARRY_FORWARD_REMOVED` | Carry-forward applied / ended |
| `BATCH_RELEASED` | Released early (`release=true`) |
| `BATCH_REOPENED` | Reopened (REOPEN) |
| `EXPORT_LOADED` | Exported (or skipped, with the reason) |
| `FILE_RULES_PASSED`, `FILE_RULES_FAILED` | Rules result |
| `BATCH_CLOSED` | Closed, with status / resolution |

### 8.15 Audit events (`CMS_ComplianceExceptionsAudit.Event_Ty`)

Emailed by NOTIFY unless marked "no".

| Event | Severity | Emailed | Meaning |
|---|---|---|---|
| `FILE_REJECTED_UNPARSEABLE` | ERROR | yes | File name does not follow the agreed format |
| `FILE_REJECTED_AMBIGUOUS_TEMPLATE` | ERROR | yes | File name fits more than one file config |
| `FILE_REJECTED_INVALID_TOKEN` | ERROR | yes | Invalid date or version in the name |
| `FILE_REJECTED_NO_BATCH` | ERROR | yes | No batch for that run type and period |
| `FILE_REJECTED_BATCH_CLOSED` | ERROR | yes | Batch closed and no valid override |
| `FILE_TYPE_NOT_SUPPORTED` | ERROR | yes | File type not supported |
| `FILE_PARSE_ERROR`, `FILE_COLUMN_COUNT_MISMATCH`, `FILE_TRAILER_COUNT_MISMATCH` | ERROR | yes | File content problems |
| `FILE_CONTENT_CHECK_FAILED` | ERROR | yes | Checks failed at load (`CHECK_ON_LOAD`) |
| `FILE_CHECK_FAILED` | WARNING | yes | FILE_CHECK found problems |
| `FILE_CHECK_PASSED` | INFO | no | FILE_CHECK passed |
| `FILE_SAME_CONTENT_OTHER_BATCH` | WARNING | yes | Same content already loaded for another batch |
| `FILE_MOVE_FAILED` | WARNING | no | File could not be moved to Archive / Error |
| `FILE_TECHNICAL_FAILURE` | ERROR | yes | System problem loading a file |
| `CORE_LOAD_ROWCOUNT_MISMATCH` | ERROR | yes | Core rows differ from staged rows |
| `SOURCE_QUERY_FAILED`, `SOURCE_DATA_REJECTED` | ERROR | yes | Internal source query failed / data does not fit |
| `SOURCE_MISSING_AT_CLOSE` | WARNING | yes | No data by the close date; closed as `DATA_NOT_PROVIDED` |
| `EXPORT_LOAD_FAILED` | ERROR | yes | Export of a batch failed (rolled back) |
| `RULES_VALIDATION_FAILED` | ERROR | yes | A data quality rule failed |
| `RULES_ENGINE_TECHNICAL_FAILURE` | ERROR | yes | Rules engine could not run |
| `INTAKE_FAILED` | ERROR | yes | Ad-hoc request could not create its batches |
| `OVERRIDE_APPROVED` | WARNING | yes | A carry-forward was applied |
| `OVERRIDE_INVALID_DETECTED` | ERROR | yes | An override cannot be applied (reason in the text) |
| `OVERRIDE_EXPIRED` | WARNING | yes | A carry-forward ran out |
| `BATCH_REOPENED` | WARNING | yes | A batch was reopened |
| `BATCH_CLOSE_BLOCKED` | WARNING | no | A manual close was refused |
| `BATCH_CLOSE_DEFERRED_LOCKED` | INFO | no | Close postponed (batch busy) |
| `CONFIG_VALIDATION_FAILED` | ERROR | yes | Configuration problem |

---

## 9. Airflow: connections, Variables, settings, DAGs, triggers

### 9.1 Airflow Connections

| Connection | Type / fields | Used by |
|---|---|---|
| Teradata (name in `td_conn_var`, e.g. `DEV`) | host, login, password; Extra `{"logmech": "LDAP"}` | Every step |
| NAS (name in `wdc_comp_oper_nas_var`) | host, login, password, port (445) | FILE_CHECK, FILE_LOAD |
| SQL Server source (name = `Src_Conn_Cd`) | host or `server\instance`, port, schema = database, login, password; Extra `{"driver": "ODBC Driver 18 for SQL Server", "encrypt": "yes", "trust_server_certificate": "yes"}` | SOURCE_LOAD |
| `smtp_default` | Airflow SMTP connection | DAG emails, NOTIFY (`NOTIFY_BACKEND = airflow`) |

### 9.2 DAGs

| Step | DAG id (example names, clone per project) | Variable |
|---|---|---|
| BATCH_CREATION | `OSSTD_CORE_CMS_COMPLIANCE_BATCH_CREATION` | `compliance_batch_creation_config_var` |
| FILE_CHECK | `OSSTD_CORE_CMS_COMPLIANCE_FILE_CHECK` | `compliance_file_check_config_var` |
| FILE_LOAD | `OSSTD_CORE_CMS_COMPLIANCE_FILE_LOAD` | `compliance_file_load_config_var` |
| SOURCE_LOAD | `OSSTD_CORE_CMS_COMPLIANCE_SOURCE_LOAD` | `compliance_source_load_config_var` |
| EXPORT_LOAD | `OSSTD_CORE_CMS_COMPLIANCE_EXPORT_LOAD` | `compliance_export_load_config_var` |
| OVERRIDE_DECISIONS | `OSSTD_CORE_CMS_COMPLIANCE_OVERRIDE_DECISIONS` | `compliance_override_decisions_config_var` |
| BATCH_CLOSE | `OSSTD_CORE_CMS_COMPLIANCE_BATCH_CLOSE` | `compliance_batch_close_config_var` |
| NOTIFY | `OSSTD_CORE_CMS_COMPLIANCE_NOTIFY` | `compliance_notify_config_var` |

A DAG never runs twice at the same time; a run that is due while another runs waits.

### 9.3 Variable keys

| Key | Required | Default | Meaning |
|---|---|---|---|
| `meta_db` | **Yes** | | Metadata database (e.g. `HSABC_DEV`) |
| `td_conn_var` | Yes | `compliance_teradata` | Teradata Connection id |
| `wdc_comp_oper_nas_var` | FILE_LOAD, FILE_CHECK | | NAS Connection id |
| `load_env` | Recommended | `DEV` | Environment: email subjects and `$env` (DEV / TEST / QA / PROD / UAT) |
| `project` | **Yes, per project** | every project | The project the DAG works on |
| `table_group`, `table`, `run_type` | No | all | Narrow the run |
| `schedule_interval` | No | none (trigger only) | Cron expression, Central time, e.g. `0 20 * * *` |
| `trigger_dags` | No | none | DAG ids to start after success (must be the project's own clones) |
| `hold` | No | none | Run types to hold, e.g. `"WEEKLY"` (SOURCE_LOAD, EXPORT_LOAD, BATCH_CLOSE) |
| `runs` | No | | List of scopes; with `name` + `schedule_interval` per entry: timed runs (section 9.6) |
| `email_recipient` | No | none | Success / failure emails of the DAG |
| `snow_assignment_group` | No | `D&AE - EDE Govt Compliance` | ServiceNow incident group on failure |
| `owner`, `tags`, `log_level` | No | `oss`, standard tags, `INFO` | |
| `fail_on_problems` | No | true (OVERRIDE_DECISIONS false) | Fail the task when the step has problems |
| `execution_timeout_minutes`, `dagrun_timeout_minutes` | No | per step / 480 | Time limits |
| `settings` | No | | Framework settings (section 9.4) |
| `gre` | FILE_RULES | from the Variable | GRE rules engine settings (`meta_db`, `text_params`, `extra_filters`, …) |

```json
{"meta_db": "HSABC_DEV", "td_conn_var": "DEV", "load_env": "DEV", "project": "CMSUNIV",
 "wdc_comp_oper_nas_var": "wdc_comp_oper_nas_dev", "schedule_interval": "0 * * * *",
 "email_recipient": "compliance-dev@example.com",
 "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_EXPORT_LOAD", "OSSTD_CORE_CMS_COMPLIANCE_OVERRIDE_DECISIONS", "OSSTD_CORE_CMS_COMPLIANCE_NOTIFY"],
 "settings": {"CHECK_MOVE_FAILED": true}}
```

### 9.4 Framework settings (`"settings": {...}`)

| Setting | Default | Meaning |
|---|---|---|
| `FILE_STORE` | `nas` | `nas` or `local` (tests) |
| `ARCHIVE_FOLDER`, `ERROR_FOLDER`, `ERROR_LOG_FOLDER`, `CONTROL_REPORT_FOLDER` | `Archive`, `Error`, `ErrorLog`, `ControlReport` | Subfolders of the inbound folder |
| `ARCHIVE_BY_YEAR` | false | Archive into `Archive/<year>/` |
| `NAS_MIN_AGE_SECONDS` | 120 | A file younger than this is left for the next run (still arriving) |
| `CHECK_MOVE_FAILED` | false | FILE_CHECK moves a failed file to `Error` |
| `CHECK_CONTENT` | true | FILE_CHECK reads the content (else name / batch only) |
| `CHECK_ON_LOAD` | false | FILE_LOAD runs the content checks itself |
| `WRITE_ERROR_FILE`, `WRITE_CONTROL_REPORT` | true | Write the error log / control report |
| `FILE_ENCODING` | `utf-8` | File encoding |
| `QUOTE_CHAR` | `"` | Quote character |
| `EMPTY_AS_NULL` | true | Empty field = NULL |
| `SUPPORTED_FILE_TYPES` | `.txt,.csv` | Accepted extensions |
| `LOAD_ENGINE` | `BATCH` | `BATCH` or `FASTLOAD` (large files; test first) |
| `INSERT_BATCH_ROWS`, `INSERT_BATCH_BYTES` | 2000, 500000 | Insert batch size |
| `RULE_ENGINE` | `gre` | `gre` or `none` |
| `GRE_ENTRYPOINT` | | `compliance_framework.export:run_gre` |
| `FILE_RULES_MODE` | `GATE` | `GATE` (failure = FAILED) or `ANNOTATE` (failure = PASSED_WITH_WARNINGS) |
| `RELEASE_TIME` | none | `HH:MM`: on the close date, export (or pull) only from that time |
| `NOTIFY_BACKEND` | `log` | `airflow` (email through `smtp_default`) or `log` |
| `NOTIFY_FROM_EMAIL` | | Sender address |
| `DEFAULT_NOTIFY_EMAILS` | | Alert recipients (the project's NOTIFY Variable) |
| `LOCK_TIMEOUT_SECONDS` | 300 | How long a step waits for a busy batch / table |
| `LOCK_TTL_MINUTES` | 240 | When a lock left by a crashed run expires |
| `ENV_VALUE` | from `load_env` | Value of `$env` if different |

### 9.5 Triggering a DAG by hand (JSON)

Any key overrides the Variable for that run:

| Key | Steps | Meaning |
|---|---|---|
| `project`, `table_group`, `table`, `run_type` | all | Scope |
| `batch` | SOURCE_LOAD, EXPORT_LOAD | One or more `Btch_ID`s, comma separated (every source of the batch) |
| `release` | SOURCE_LOAD, EXPORT_LOAD | `true` = release held batches of the scope now |
| `hold` | SOURCE_LOAD, EXPORT_LOAD, BATCH_CLOSE | Run types to hold for this run |
| `period`, `lookback_days`, `lookback_weeks` | BATCH_CREATION | Create batches for a given period now |
| `as_of` | all | Run as if it were that date / time (missed day) |
| `run` | timed DAGs | Which `runs` entries to use, e.g. `"MNT"` |
| `settings`, `gre`, `log_level`, `fail_on_problems` | all | |

```json
{"project": "CMSUNIV", "run_type": "CMS", "batch": "20261019_CMSUNIV_ODAG3_CMS", "release": "true"}
```

**Admin commands** run from the command line on an Airflow worker, in the DAGs folder. The Teradata login is set in
that shell only (never saved in a file):

```bash
export TERADATA_HOST=<host> TERADATA_USER=<login> TERADATA_PASSWORD=<password> TERADATA_LOGMECH=LDAP
export FRAMEWORK_METADATA_SCHEMA=HSABC_DEV FRAMEWORK_ENVIRONMENT=DEV
```

| Command | What it does |
|---|---|
| `python -m compliance_framework.cli validate-config` | Checks every configuration table (section 11.1) |
| `python -m compliance_framework.cli health` | Open batches past their close date and other health checks |
| `python -m compliance_framework.cli locks` | Locks held by running (or crashed) steps |
| `python -m compliance_framework.cli release-lock --key <Lock_Key>` | Removes a lock left by a run that no longer exists |
| `python -m compliance_framework.cli close-batch --btch-id <Btch_ID> --closed-by <you> [--src-id <Src_ID>]` | Closes a batch (or one source of it) by hand |
| `python -m compliance_framework.cli test-connection --source <Src_Conn_Cd> --db-type SQLSERVER` | Tests an internal source's connection (the Airflow Connection must be available, e.g. `AIRFLOW_CONN_<ID>`) |
| `python -m compliance_framework.cli show-config` | The settings in effect and where each came from |
| `python -m compliance_framework.cli run --module <STEP> --project CMSUNIV [--as-of <date>]` | Runs any step by hand, also `FILE_RULES` |

### 9.6 One DAG at several times (timed runs)

```json
{"meta_db": "HSABC_DEV", "td_conn_var": "DEV", "load_env": "DEV", "project": "CMSUNIV", "schedule_interval": null,
 "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_NOTIFY"],
 "runs": [{"name": "DAILY", "run_type": "DAILY", "schedule_interval": "0 20 * * *", "settings": {"RELEASE_TIME": "20:00"}},
          {"name": "MNT", "run_type": "MNT", "schedule_interval": "0 6 * * *"}]}
```

A scheduled run uses the entry of its time; a manual run uses the entries named in `run` (else the Variable's own
scope) plus what the trigger gives.

---

## 10. Set up a new project – step by step

```
 1 Tables in the metadata DB ──► 2 Project, sources, run types ──► 3 Crosswalk
        │                                                              │
        ▼                                                              ▼
 4 Staging / core / export tables (DBA) ──► 5 File / query configs ──► 6 Derived columns
                                                                       │
 7 File checks ──► 8 Export, rules (optional) ──► 9 Airflow Connections and Variables
                                                                       │
 10 validate-config ──► 11 First cycle in DEV ──► 12 Production
```

1. **Metadata tables** (once per environment): run `airflow_resources/sql/schema.sql` with `{{META_DB}}` replaced.
2. **Project, sources, run types**: sections 5.1 to 5.4 (and 5.2 for table groups).
3. **Report dates and crosswalk**: one `ComplianceRunTypePeriod` row per scheduled run type (5.5), then one crosswalk
   row per table + source listing its run types (5.6), plus rows for one run type where a source differs.
4. **Staging, core, export tables** (DBA): file columns, `Btch_ID`, a source column, derived columns; export =
   core's columns + `Exp_Btch_ID` (optional).
5. **How data arrives**: a file config per file source (5.7), a query config per internal source (5.8).
6. **Derived columns**: section 5.10 – at least `SOURCE_ID`; usually `RUN_TYPE`, `VERSION`, `FILE_NAME`,
   `LOAD_TIMESTAMP`, `ROW_SEQUENCE`.
7. **File checks**: section 5.9 – header names, record types / trailer, value checks.
8. **Optional**: export (5.11), rules (5.12), calendar (5.6a).
9. **Airflow**: Connections (9.1); clone the 8 DAG files with project names (e.g. `OSSTD_CORE_PRJB_COMPLIANCE_*`)
   and their Variables (9.3): every Variable sets `project`; `trigger_dags` names the project's own clones; NOTIFY
   sets `DEFAULT_NOTIFY_EMAILS`. Keep one NOTIFY without `project` for events of no project.
10. **Validate**: `validate-config` from the command line (9.5); fix every ERROR.
11. **First cycle in DEV**: BATCH_CREATION → put a test file / source rows → FILE_LOAD / SOURCE_LOAD → EXPORT_LOAD
    → BATCH_CLOSE → NOTIFY; check with section 12. Try a bad file, a missing source, a late file.
12. **Production**: same rows in the PROD metadata DB (`$env` makes database names and paths follow).

---

## 11. Operating procedures (how to …)

**11.1 Check the configuration.** `python -m compliance_framework.cli validate-config` (9.5). ERRORs must be fixed; WARNINGs explain
something to look at.

**11.2 Create batches for a missed day.** Trigger BATCH_CREATION with `{"project": "CMSUNIV", "as_of": "2026-10-19"}`.

**11.3 Request an ad-hoc batch.** Insert into `ComplianceRequestInTake` (6.1); the next BATCH_CREATION creates it.
Ad-hoc batches are pulled / exported only when released (11.6).

**11.4 A file was rejected.** Find the reason (12.3); the file is in `<inbound>\Error\` and its error log in
`<inbound>\ErrorLog\<name>_errors.txt`. Ask the source to fix and resend, or fix the configuration, then put the file
back into the inbound folder (any name collision gets `_1`, `_2`).

**11.5 A source will not send (carry-forward).** Before the batch's close run: insert an approved `REUSE` override
(6.2); OVERRIDE_DECISIONS applies it. The run type needs `Carry_Fwd_Ind = 1`.

**11.6 Release early / export ad hoc.** Trigger EXPORT_LOAD (and SOURCE_LOAD for internal sources) with
`{"project": "CMSUNIV", "run_type": "WEEKLY", "release": "true"}` (or `"batch": "<Btch_ID>"`). Released batches with
data close on the next BATCH_CLOSE.

**11.7 Hold a run type for business reasons.** Add `"hold": "WEEKLY"` to the SOURCE_LOAD, EXPORT_LOAD and BATCH_CLOSE
Variables. Remove it to go back to normal. Release single batches with 11.6 meanwhile.

**11.8 A late file for a closed batch.** Without data: approved `LATE_ARRIVAL`; with data: approved `CORRECTION`.
Then put the file in the inbound folder. Or reopen (11.9).

**11.9 Reopen a closed batch.** Approved `REOPEN` override (6.2); the next OVERRIDE_DECISIONS reopens it; it closes
after new data, or with `close-batch`.

**11.10 Re-pull an internal source.** Trigger SOURCE_LOAD with `{"batch": "<Btch_ID>"}` (replaces the rows; a closed
batch needs its override).

**11.11 Re-export a batch.** Trigger EXPORT_LOAD with `{"batch": "<Btch_ID>"}`.

**11.12 Close a batch by hand.** `close-batch` from the command line (9.5); every open source of the batch, or `--src-id` for
one. Refused before the close date unless released.

**11.13 Run the step for one table group / table / run type.** Trigger with `table_group` or `table`, and/or
`run_type`.

**11.14 An alert email arrived.** Look up the event in 8.15 and the batch in 12.1 / 12.2.

**11.15 A step waits or says "locked".** `locks` from the command line (9.5); if the owner run no longer exists:
`release-lock --key <Lock_Key>`.

**11.16 Change a version.** For every run type of the source: end the crosswalk row (`Effective_End_Dt_Key`) and
add a new row with the new `Cmplnc_Vrsn` from the next day. For one run type only: add a row for that run type with
the new version from its start date (it replaces the list's entry). Update `File_Vrsn_Cd` if file names change.

**11.17 Stop a source or table.** Set `Active_Ind = 0` on its crosswalk rows (and file / query config). To stop
one run type of a source: remove it from the row's list, or add a row for that run type with `Include_Ind = 0`.

**11.18 Add a run type to a source.** Add it to the `Run_Ty` list of the source's crosswalk row (and a
`ComplianceRunTypePeriod` row if the run type is new for the project). Run validate-config.

**11.19 Change the report dates of a run type.** Update `Rpt_Period_Cd` of its `ComplianceRunTypePeriod` row (the
project's `*` row, or add a row for one table). Batches already created keep their dates.

---

## 12. Monitoring queries

**12.1 Batches of a period**

```sql
SELECT Btch_ID, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key, Req_Stat, Resolution_Ty, Batch_Close_Ind
FROM HSABC_DEV.ComplianceRequestControl
WHERE Project_Cd = 'CMSUNIV' AND Req_Dt_Key >= CURRENT_DATE - 7
ORDER BY Req_Dt_Key, Btch_ID, Src_ID;
```

**12.2 Timeline of a batch**

```sql
SELECT c.Src_ID, d.Event_Dtts, d.Event_Ty, d.Actor, d.Load_ID, d.Event_Txt
FROM HSABC_DEV.ComplianceRequestFileDetail d JOIN HSABC_DEV.ComplianceRequestControl c ON c.Req_ID = d.Req_ID
WHERE c.Btch_ID = '20261019_CMSUNIV_ODAG3_DAILY'
ORDER BY c.Src_ID, d.Detail_ID;
```

**12.3 Rejected or failed loads (last 7 days)**

```sql
SELECT Load_ID, File_Path, Btch_ID, Load_Stat, Quarantine_Rsn_Cd, Error_Txt, Updated_Dtts
FROM HSABC_DEV.ComplianceFileLoad
WHERE Load_Stat IN ('QUARANTINED', 'RULES_FAILED', 'FAILED_TECHNICAL') AND Updated_Dtts >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
ORDER BY Updated_Dtts DESC;
```

**12.4 Open batches past their close date**

```sql
SELECT c.Btch_ID, c.Src_ID, c.Run_Ty, c.Req_Dt_Key, c.Req_Dt_Key + (r.SLA_Days - 1) AS Close_Dt, c.Req_Stat
FROM HSABC_DEV.ComplianceRequestControl c JOIN HSABC_DEV.ComplianceRunType r ON r.Run_Ty = c.Run_Ty
WHERE c.Batch_Close_Ind = 0 AND c.Req_Dt_Key + (r.SLA_Days - 1) < CURRENT_DATE
ORDER BY Close_Dt;
```

**12.5 Sources missing at close (last 30 days)**

```sql
SELECT Event_Dtts, Project_Cd, Table_Nm, Src_ID, Run_Ty, Btch_ID, Event_Txt
FROM HSABC_DEV.CMS_ComplianceExceptionsAudit
WHERE Event_Ty = 'SOURCE_MISSING_AT_CLOSE' AND Event_Dtts >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
ORDER BY Event_Dtts DESC;
```

**12.6 Unsent alerts** (NOTIFY not running or email failing)

```sql
SELECT Event_ID, Event_Ty, Sevrty, Project_Cd, Btch_ID, Event_Dtts FROM HSABC_DEV.CMS_ComplianceExceptionsAudit
WHERE Notified_Ind = 0 ORDER BY Event_Dtts;
```

**12.7 Overrides and their state**

```sql
SELECT o.Ovrd_ID, o.Override_Ty, o.Btch_ID, c.Src_ID, o.Apprvl_Stat, o.Valid_Thru_Dt_Key, o.Reviewed_By, o.Rsn_Txt
FROM HSABC_DEV.ComplianceBatchOverride o JOIN HSABC_DEV.ComplianceRequestControl c ON c.Req_ID = o.Req_ID
ORDER BY o.Ovrd_ID DESC;
```

**12.8 Internal sources waiting for data**

```sql
SELECT c.Btch_ID, c.Src_ID, d.Event_Dtts, d.Event_Txt
FROM HSABC_DEV.ComplianceRequestFileDetail d JOIN HSABC_DEV.ComplianceRequestControl c ON c.Req_ID = d.Req_ID
WHERE d.Event_Ty = 'SOURCE_NO_DATA_YET' AND c.Req_Stat = 'REQUEST_CREATED'
ORDER BY d.Event_Dtts DESC;
```

---

## 13. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| File rejected `FILE_REJECTED_UNPARSEABLE` | Name does not match `Src_File_Nm_Tmplt` | Compare the name with the template (8.5); fix the template or ask for the right name |
| `FILE_REJECTED_NO_BATCH` | No batch for that run type / report dates | Check the crosswalk and schedule; create with `as_of` / `period` or an intake request |
| `FILE_REJECTED_BATCH_CLOSED` | Batch already closed | `LATE_ARRIVAL` / `CORRECTION` / `REOPEN` override, then resend |
| Version error in the name | `{VERSION}` ≠ `File_Vrsn_Cd` | Fix `File_Vrsn_Cd` or the file name |
| Column count / header problems | File layout changed | Check `HEADER_NAMES` / `COLUMN_COUNT` / `TARGET_COLUMNS` |
| Load fails "must return one row" (SQL column) | `{btch_id}` lookup returns one row per source | Add `AND Src_ID = {src_id}` or use `{req_id}` |
| "needs a column mapped to SOURCE_ID" | Staging / core / export table without source column | Add the column and a `SOURCE_ID` row (5.10) |
| Batch not exported | Not due yet, held, or ad hoc not released | Step report `held` list; release (11.6) or wait |
| Batch closed as `DATA_NOT_PROVIDED` / `MISSING` | Nothing by the close date | Expected; late data: `LATE_ARRIVAL` or `REOPEN` |
| Carry-forward refused | Batch closed, run type without `Carry_Fwd_Ind`, no earlier batch with data | Apply before the close run; check 12.7 and the `OVERRIDE_INVALID_DETECTED` text |
| Internal source `WAITING` | No rows yet before the close date | Normal; tried again each run |
| Step "locked" | Another run is working on it, or a crashed run | Wait; else 11.15 |
| No alert emails | NOTIFY not scheduled / not triggered, no `DEFAULT_NOTIFY_EMAILS`, no `smtp_default` | Check 12.6 and the NOTIFY Variable |
| Rules never run | Table has an export and the batch is not exported yet | Normal: rules wait for the export |

---

## 14. Glossary

| Term | Meaning |
|---|---|
| Project | A compliance program, e.g. CMS Universes (`Project_Cd`) |
| Table | A dataset of the project, e.g. ODAG3 (`Table_Nm`); has staging, core and optionally export tables |
| Table group | Tables run together, e.g. ODAG1–5 |
| Source | Who provides the data: an FDR sending files, or an internal system read by a query (`Src_ID`) |
| Run type | How often / why batches are made: DAILY, WEEKLY, MNT, ad hoc (`Run_Ty`) |
| Crosswalk | Which source sends which table for which run type, from when, at which version |
| Batch | One table + run type + report period + request date; one row per source (`Btch_ID`, `Req_ID`) |
| Report period | The dates the data is about (`Rpt_Start_Dt_Key` – `Rpt_End_Dt_Key`) |
| Request date | The day the batch was created (`Req_Dt_Key`) |
| Close date / due date | Request date + SLA days − 1: when the batch closes and when its data is exported |
| Staging / core / export | Landing table of a load; the table's data of record; the copy for downstream / rules |
| Carry-forward (REUSE) | Using an earlier batch's data for a batch without data |
| Late arrival / correction | Data for a closed batch, accepted with an override |
| Reopen | Opening a closed batch again |
| Release / hold | Let a batch go before its date / keep a run type back |
| Timeline | Every event of a batch (`ComplianceRequestFileDetail`) |
| Exceptions audit | Problems and approvals, emailed (`CMS_ComplianceExceptionsAudit`) |
| Step report | The summary each step writes at the end of its Airflow log |
