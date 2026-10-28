# Compliance Batch Framework – Operational Runbook

**Audience:** business analysts, operations and support staff who set up and run projects on the framework, without
reading the code. **Version:** framework 0.3.77 (Airflow 3 / Teradata).

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
- The audit columns `Load_Dtts`, `LoadUser_ID`, `LastUpdt_Dtts`, `LastUpdt_ID` are filled by the database. Leave
  them out of every INSERT. Columns marked **(framework)** are written by the framework only: never insert or update
  them.
- After any configuration change, run **validate-config** (section 2.11). It reports every problem it finds.

---

## Contents

1. What the framework does (business view)
2. The daily flow (DAGs) – diagram, order, what each DAG does
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
15. CMSUNIV setup at a glance
16. Upgrading an existing installation

---

## 1. What the framework does (business view)

The framework collects data for compliance reporting from many **sources** (external vendors such as FDRs that
send files, and internal systems that are read with a query), for many **tables** (datasets, e.g. ODAG3) of a
**project** (e.g. the CMS Universes), on a schedule (**run types** such as DLY, WKLY, MNT) or on request
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
                 NOTIFY  ◄──── triggered by BATCH_CLOSE (+ its own schedule)
                   emails the open audit events

 OVERRIDE_DECISIONS  (triggered by FILE_LOAD, or scheduled)
   applies approved carry-forwards (REUSE) and reopens (REOPEN), expires old ones

 BATCH_CLOSE  (nightly, e.g. 22:00) ──► BATCH_CREATION (next day's batches) and NOTIFY
   closes every batch past its close date (with or without data)

 Command line (by hand)  validate-config, health, locks, close-batch … (2.11)
 FILE_RULES  (started by EXPORT_LOAD, which waits for it) data quality rules of each exported batch (2.12)
```

### 2.1 Order of a day

| # | DAG | Runs | Starts automatically |
|---|---|---|---|
| 1 | BATCH_CREATION | Start of day (e.g. 06:00), and after BATCH_CLOSE | — |
| 2 | FILE_CHECK (optional) | When files are expected (e.g. every 15 min) | FILE_LOAD |
| 3 | FILE_LOAD | After FILE_CHECK, or on its own (e.g. hourly) | OVERRIDE_DECISIONS, EXPORT_LOAD |
| 4 | SOURCE_LOAD | After the source systems have loaded their own data (e.g. 06:30) | EXPORT_LOAD |
| 5 | OVERRIDE_DECISIONS | After FILE_LOAD (or scheduled) | — |
| 6 | EXPORT_LOAD | After FILE_LOAD / SOURCE_LOAD, and once in the evening (e.g. 20:00) | FILE_RULES (and waits for it) |
| 6a | FILE_RULES | Started by EXPORT_LOAD | — |
| 7 | BATCH_CLOSE | End of day (e.g. 22:00) | BATCH_CREATION, NOTIFY |
| 8 | NOTIFY | After BATCH_CLOSE, and on its own schedule (e.g. hourly) | — |

Rules: batches exist before files and pulls arrive (1 before 2 – 4); carry-forwards are applied before the close
(5 before 7); the evening export runs before the close (6 before 7); FILE_LOAD and SOURCE_LOAD are independent (a
table loaded by both waits its turn); keep FILE_LOAD out of the window between BATCH_CLOSE and BATCH_CREATION, or a
file for a closed period is rejected. Until `schedule_interval` is set, every chain starts only when triggered by
hand.

### 2.2 What every DAG does the same way

- **Tasks:** the step's task (one per `runs` entry), a `trigger_<dag id>` task per `trigger_dags` entry, then
  `success_email` / `fail_email` to `email_recipient`. FILE_CHECK also has `check_passed` (before the triggers) and
  `file_check_result` (last; fails when the check failed).
- **Result of a step** (the JSON report at the end of the task log, the "step report"):

| Exit | Meaning | Task | Next DAGs |
|---|---|---|---|
| 0 | Done, nothing to report | Success | Started |
| 1 | Done, with problems the step reported (a rejected file, a batch not loaded, a failed export) | Fails at once, no retry, ServiceNow incident + failure email (OVERRIDE_DECISIONS, and any DAG with `fail_on_problems = false`: success) | Not started |
| 2 | Could not run (database down, a missing table, a bad Variable) | Fails, retried `retries` times after 5 minutes, then incident + email | Not started |

- **Safe to repeat:** every step can run again at any time and only does what is still to do (a batch is never
  created twice, a file never loaded twice, an export replaces its own rows).
- **Never twice at once:** a DAG waits for its own previous run; different DAGs can overlap (batch and table locks
  keep them apart; a busy staging table makes a file or pull `LOAD_INCOMPLETE`, picked up by the next run).
- **Scope:** `project`, `table_group`, `table`, `run_type`, `source` narrow what a run works on (Variable, `runs`
  entry or trigger: 9.3, 9.5). `as_of` runs it as if it were another day (a missed day).
- **Logs:** each step writes its progress (`FILE_LOAD | <file> | load 1 | 4 data row(s) staged …`), every configured
  SQL with its parameters (section 13), and the step report.

### 2.3 BATCH_CREATION

Creates the batches that are due: one row per source in `ComplianceRequestControl`.

| Where batches come from | When | Report dates |
|---|---|---|
| A `SCHEDULED` run type with schedule codes (`Sched_Cd` …, 8.2) | On its scheduled day, moved by `Sched_Offset_Days`; a missed day is still created within `Sched_Window_Days`, with the scheduled day's dates | `ComplianceRunTypePeriod` / crosswalk (5.5, 8.4), counted from the scheduled day |
| A run type with `Batch_Schedule_Sql_Txt` | When the statement returns a row | From that row, or the period row |
| `ComplianceBatchCalendar` | On the calendar dates | The calendar row's dates |
| An `ADHOC` run type with a schedule (e.g. IN1) | As a scheduled one, for the sources whose crosswalk row lists it | As a scheduled one |
| `ComplianceRequestInTake` | Every day of the request window, one batch per day | The request's dates |

- **Which sources:** every crosswalk row active and effective on the batch's date that lists the run type
  (`Include_Ind = 1`); one batch per table + run type + report period + day, one row per source.
- **Batch ID:** `<YYYYMMDD of the day>_<project>_<table>_<run type>`, e.g. `20261020_CMSUNIV_ODAG3_DLY`.
- **Version:** the `Cmplnc_Vrsn` of the crosswalk row (or of the intake request).
- **Close date:** `Req_Dt_Key + SLA_Days - 1`.
- **Options:** `period` (a period code: create batches with those dates now), `lookback_days` / `lookback_weeks`
  (catch up missed days), `run_type`, `table` / `table_group`, `as_of`.
- **Problems (exit 1):** a configuration error met while creating (a bad period code, a failing SQL), a failed
  intake request (`INTAKE_FAILED`). The other batches are still created.

### 2.4 FILE_CHECK (optional)

Checks the files waiting in the inbound folders of the scope's file configs; **loads nothing**.

- Files modified in the last `NAS_MIN_AGE_SECONDS` (120) are left for the next run (still arriving).
- Per file: the name against `Src_File_Nm_Tmplt` (exactly one config must match), the open batch of its run type and
  dates, the checks of `ComplianceFileCheckConfig` (5.9).
- Passed: `FILE_CHECK_PASSED` (not emailed). Failed: `FILE_CHECK_FAILED` (emailed by NOTIFY), an error log in
  `ErrorLog/`, and with `CHECK_MOVE_FAILED = true` the file moves to `Error/` (so FILE_LOAD does not pick it up).
- **One failed file fails the task** (exit 1: ServiceNow incident + failure email). When at least one file passed,
  the `check_passed` task still starts the triggered FILE_LOAD, so the good files load right away; with no passed
  file it is skipped. `file_check_result` then keeps the DAG run failed. Keep `CHECK_MOVE_FAILED = true` so
  FILE_LOAD does not pick up (and reject again) the failed file. `"fail_on_problems": false` makes the task succeed
  instead (no incident or failure email).
- Options: the scope, `as_of`. Settings: `CHECK_CONTENT`, `CHECK_MOVE_FAILED` and the file settings of 9.4.

### 2.5 FILE_LOAD

Loads every waiting file of the scope's file configs: name → batch → checks → staging → core (5.7, "How a file goes
from the NAS to core").

| What happens to a file | `Load_Stat` | File moves to | Batch |
|---|---|---|---|
| Loaded, with rows | `FILE_LOADED` | `Archive/` (`Archive/<year>/` with `ARCHIVE_BY_YEAR`) | `SOURCE_DATA_READY` (`NEW_FILE`) |
| Loaded, no data rows | `FILE_LOADED` | `Archive/` | `SOURCE_DATA_READY_NO_DATA` (`NO_DATA`) |
| A newer file for the same batch before the close | the earlier load becomes `RELOADED` | | data replaced |
| A name, batch, check or type problem | `REJECTED` (`Reject_Rsn_Cd` says why) | `Error/`, error log in `ErrorLog/` | unchanged |
| Batch closed, no approved override | `REJECTED` (`FILE_REJECTED_BATCH_CLOSED`) | `Error/` | unchanged |
| Closed batch with an approved `LATE_ARRIVAL` / `CORRECTION` | `FILE_LOADED` | `Archive/` | stays closed, data replaced |
| Staging table busy | `LOAD_INCOMPLETE` (`STAGING_TABLE_BUSY`) | stays | picked up by the next run |
| System problem | `PROCESS_FAILURE` | stays | tried again (retry, next run) |

- A file is identified by its path, size and time: the same name delivered again is a new delivery; the same
  content for another batch is loaded and reported (`FILE_SAME_CONTENT_OTHER_BATCH`).
- Before core: the NOT NULL and type checks (5.7 step 4); staging and core columns pair by name, then by order.
- One control report line per file goes to `ControlReport/`.
- **Problems (exit 1):** any rejected file. The task fails (incident + email) and the triggered EXPORT_LOAD /
  OVERRIDE_DECISIONS do not start; the other files of the run are still loaded. `"fail_on_problems": false` keeps
  the chain going.
- Options: the scope, `as_of`; from a trigger also `share`, `folder`, `file` (one file). Settings: the file and load
  settings of 9.4.

### 2.6 SOURCE_LOAD

Pulls every open batch whose source has a `ComplianceInternalSourceConfig` row (5.8), with its query.

| Pull result | `Load_Stat` | Batch |
|---|---|---|
| Rows | `FILE_LOADED` | `SOURCE_DATA_READY` |
| No rows, before the close date | `NO_DATA_YET` (report: `WAITING`) | unchanged; pulled again on the next runs |
| No rows, on / after the close date | `FILE_LOADED` | `SOURCE_DATA_READY_NO_DATA`; still pulled again until it closes |
| Query or connection failed | `REJECTED` (`SOURCE_QUERY_FAILED`, with the driver's message) | unchanged |
| Columns do not fit staging / core | `REJECTED` (`SOURCE_DATA_REJECTED` / `CORE_TYPE_MISMATCH`) | unchanged |

- Ad-hoc run types (IN1, CMS) are pulled like scheduled ones, no release needed. Held run types (`hold`) are
  skipped and listed under `held` in the step report.
- The query's column names fill the staging columns of the same name (`AS` aliases); a column it returns wins over
  its `ComplianceTargetColumnConfig` row, except the batch, source and load columns.
- Each query is logged with its parameters and a copy with the values written in.
- Options: the scope, `batch`, `release`, `hold`, `as_of`.

### 2.7 OVERRIDE_DECISIONS

Applies the approved rows of `ComplianceBatchOverride` (6.2): `REUSE` (carry the data of an earlier closed batch
forward; run type with `Carry_Fwd_Ind = 1`; before the close) and `REOPEN` (open a closed batch again until new data
arrives); expires overrides past `Valid_Thru_Dt_Key`. `LATE_ARRIVAL` / `CORRECTION` are used by FILE_LOAD directly.
An override that cannot apply is reported (`OVERRIDE_INVALID_DETECTED`, emailed) and the task still succeeds
(`fail_on_problems` false). Options: the scope, `as_of`.

### 2.8 EXPORT_LOAD

Copies the data of each batch that is due into the table's export table (`ComplianceExportConfig`, 5.11), by
columns or by calling the export procedure with the batch's values.

- **Due:** every run type (`SCHEDULED` and `ADHOC`) from the close date on (from `RELEASE_TIME` that day, if set),
  or earlier when released. Held run types wait.
- **Which data:** the newest batch of the period whose resolution is `NEW_FILE`, `CARRY_FORWARD` or `NO_DATA`. A
  re-export replaces the batch's own export rows (`Btch_ID` + source column); a batch is exported again only when it has new
  data since its last export.
- **How:** procedure, custom SELECT (`Exp_Sql_Txt`) or plain copy, with optional pre / post statements once per batch
  (5.11).
- Timeline `EXPORT_LOADED`; a failure is rolled back and reported (`EXPORT_LOAD_FAILED`, exit 1).
- Options: the scope, `batch`, `release`, `hold`, `as_of`.

### 2.9 BATCH_CLOSE

Closes every open batch whose close date has come:

| Batch at the close | Closed as | Alert |
|---|---|---|
| Data loaded or carried forward | `REQUEST_COMPLETE` (`NEW_FILE` / `CARRY_FORWARD`) | — |
| The source said "no data" | `DATA_NOT_PROVIDED` (`NO_DATA`) | — |
| Nothing arrived | `DATA_NOT_PROVIDED` (`MISSING`) | `SOURCE_MISSING_AT_CLOSE` (emailed) |
| Being loaded right now | stays open, closed by the next run | `BATCH_CLOSE_DEFERRED_LOCKED` (not emailed) |

Held run types (`hold`) and reopened batches still waiting for data stay open. A batch of an `ADHOC` run type with
data (`NEW_FILE` / `CARRY_FORWARD`) stays open until it is released, so it can still be pulled and exported again; one
without data closes as above. `"release": "true"` releases the scope's open batches with data and closes them in the
same run (11.6). A file after the close needs a `LATE_ARRIVAL` (or `REOPEN`) override. Options: the scope, `hold`,
`release`, `as_of`. It starts BATCH_CREATION (the next day's
batches) and NOTIFY.

### 2.10 NOTIFY

Emails every alert of `CMS_ComplianceExceptionsAudit` not sent yet (`Notified_Ind = 0`; the events marked "emailed"
in 8.15), one email per event, to the Variable's `email_recipient`, then marks it sent; an email that fails stays
for the next run. Without `project` it sends the events of every project and of none. The DAGs' success / failure
emails do not replace it: many alerts (a rejected file, a missing source, a stuck lock) do not fail any DAG.

### 2.12 FILE_RULES (data quality rules)

Runs the GRE rules of each **exported batch**, one batch at a time, by calling the GRE code in `<dags>/rules_engine`
directly (no other DAG). EXPORT_LOAD starts it and **waits until it ends** (`wait_for_triggered`), so the export
tables are not written while their rules read them; FILE_RULES runs in the Airflow pool `gre_rules` (1 slot), so
two rules runs never write the GRE tables at the same time.

| For each batch | Value |
|---|---|
| Which batches | Batches whose loaded data has not had its rules yet; a table with an export table only after the batch's export |
| Rule group / variant | The batch's table / run type (`CDAG2` / `MNT`): the GRE runs the group's rules with that variant and those without one. A `ComplianceRuleBinding` row (5.12) names other groups |
| `run_key` | The batch ID: a rerun of the batch refreshes its results and exceptions; `gre_results` / `gre_exceptions` join to `ComplianceRequestControl` by `run_key = Btch_ID` |
| Rows checked | Only the batch's: `extra_filters` `<batch column> = <Btch_ID>` (`gre.batch_column`, default `Btch_ID`) |
| `text_params` | `btch_id`, `run_ty`, `table_nm`, `project_cd`, report dates (for `{name}` in a rule), and `run_type` / `batch_schedule` for `gre_exceptions` |
| `project_name` / `process_name` | The project (`CMSUNIV`); recorded by the GRE |

| Result | `Rules_Stat` of the batch's loads | Timeline / alert | Task |
|---|---|---|---|
| All rules passed | `PASSED` (`PASSED_WITH_WARNINGS` under `ANNOTATE`) | `FILE_RULES_PASSED` | Success |
| A rule failed | `FAILED` | `FILE_RULES_FAILED`, `RULES_VALIDATION_FAILED` (emailed) | Success (a result, not an error) |
| The group has no active rules | `SKIPPED` | — | Success |
| The batch has no data rows | `SKIPPED` (the GRE is not called) | — | Success |
| A rule could not run, or the GRE failed | `ERROR`, tried again on the next run | `RULES_ENGINE_TECHNICAL_FAILURE` (emailed once) | Fails at the end; the other batches still run |

Options: the scope (`project`, `table_group`, `table`, `run_type`, `source`), `as_of`. Each rule's final SQL is in the task log.

### 2.11 Command line (admin)

`validate-config` (checks all configuration), `health` (stuck loads, rejected counts), `locks` / `release-lock`,
`close-batch` / `close-batches`, `test-connection`, `show-config`, `notify`, `run --module <STEP>`: run from a worker
(deployment guide, "Admin commands").

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

`REQUEST_COMPLETE` and `DATA_NOT_PROVIDED` are final ("closed") statuses.

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

### 5.0 How to make entries (all configuration tables)

| Rule | Detail |
|---|---|
| Order of the inserts | `ComplianceProject` → `ComplianceTable` → `ComplianceSourceSystem` → `ComplianceRunType` → `ComplianceRunTypePeriod` → `ComplianceDataSetSourceXwalk` → `ComplianceSourceFileConfig` / `ComplianceInternalSourceConfig` → `ComplianceFileCheckConfig` → `ComplianceTargetColumnConfig` → `ComplianceExportConfig` → `ComplianceRuleBinding`. Requests and overrides (section 6) when needed |
| Codes | Upper case letters, digits and `_`; not case sensitive. Lengths: project 30, table 63, source 30, run type 20, version 10 |
| Run type names | Short codes as they appear in file names and batch IDs: `DLY`, `WKLY`, `MNT`, `IN1`, `CMS` (CMSUNIV). The schedule itself is in `Sched_Cd` (`DAILY`, `WEEKLY`, `MONTHLY`), not in the name |
| Lists | Comma separated without spaces: `DLY,WKLY,MNT`, `H,D,T` |
| Dates | `DATE '2026-10-20'`; open end = `NULL` (crosswalk `Effective_End_Dt_Key`, overrides) |
| A quote inside SQL text | Doubled, as in any Teradata string: `'SELECT ... WHERE Status = ''A'''` |
| SQL text | One `SELECT` (no `;` in the middle). `{name}` placeholders (8.9) are passed as parameters: write `{rpt_start}`, never `'{rpt_start}'` |
| `$env` | In database names and NAS paths: replaced by `load_env` (`CMSUNIV_STAGE_$ENV_T` = `CMSUNIV_STAGE_DEV_T` in DEV); not inside SQL text |
| `*` | "Every" where the column allows it (`Table_Nm`, `Src_ID`, `Run_Ty`, `Check_Field_Nm`); a specific row wins over `*` |
| Switching off | `Active_Ind = 0` (configuration rows are not deleted); in the crosswalk also `Include_Ind = 0` with a start date to stop one run type from a day |
| Changing a row | `UPDATE` it. A change applies from the next run, also to open batches (their report dates do not change). For a change from a given day use the crosswalk's effective dates |
| Audit columns | `Load_Dtts`, `LoadUser_ID`, `LastUpdt_Dtts`, `LastUpdt_ID` are filled by the database: leave them out of an INSERT |
| Framework tables | `ComplianceRequestControl`, `ComplianceFileLoad`, `ComplianceRequestFileDetail`, `CMS_ComplianceExceptionsAudit`, `ComplianceLock`: not changed by hand, except as a procedure in section 11 says |
| Staging / core / export tables | Yours (DBA). Staging can be all text; core can be typed (converted on the way, 5.7). Columns with the same name pair by name, the others by order |
| After every change | Run `validate-config` (2.11) and fix every ERROR |


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
VALUES ('DLY', 'Daily monitoring for the previous day', 'SCHEDULED', 1, 0, 'DAILY', 1, 1);
-- Weekly, created Sunday, closes Friday
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Window_Days, Active_Ind)
VALUES ('WKLY', 'Weekly, previous Monday to Sunday', 'SCHEDULED', 6, 0, 'WEEKLY', 'SUN', 2, 1);
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
| `Rpt_Period_Cd` | One of the two | Period code (section 8.4) | Report dates, counted from the batch's scheduled date |
| `Rpt_Dt_Sql_Txt` | One of the two | One `SELECT` (section 8.3) | For dates no code expresses: returns `rpt_start` and `rpt_end`. A row has a code or SQL, not both |
| `Active_Ind` | Default 1 | `1`, `0` | |

Ad-hoc run types need no row (their dates come from the request). Careful: an ad-hoc run type that has a schedule
creates scheduled batches for **every** source that finds a period; when only some sources are scheduled, give it
no row here and put `Rpt_Period_Cd` on those sources' crosswalk rows.

```sql
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'DLY', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'WKLY', 'PREV_CALENDAR_WEEK', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'MNT', 'PREV_CALENDAR_MONTH', 1);
-- WKLY2: the Monday-Sunday week 5 weeks back
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', '*', 'WKLY2', 'RANGE(WEEK,5,1)', 1);
-- ODAG5 reports the quarter for MNT instead of the month
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind) VALUES ('CMSUNIV', 'ODAG5', 'MNT', 'PREV_CALENDAR_QUARTER', 1);
```

### 5.6 ComplianceDataSetSourceXwalk (crosswalk)

The heart of the setup: **one row per table + source**, listing the run types the source gets batches for, with
the period the row is valid for and the compliance version.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | Existing codes | This source sends this table |
| `Run_Ty` | Yes | One run type, or a list `DLY,WKLY,MNT` (up to 200 characters) | The run types it sends. Only the listed ones: a run type added later applies to no source until it is listed |
| `Effective_Start_Dt_Key` | Yes | Date | First run date the row applies |
| `Effective_End_Dt_Key` | No | Date | Last run date (empty = open ended). End a row and add a new one to change the version |
| `Cmplnc_Vrsn` | Yes | Up to 10, e.g. `2200` | The compliance version; stored on each batch, written to the `VERSION` column |
| `Rpt_Period_Cd` | No | Period code (section 8.4) | Only when this row's dates differ from `ComplianceRunTypePeriod` |
| `Rpt_Dt_Sql_Txt` | No | One `SELECT` | Fallback for dates no code expresses (section 8.3). Set this or `Rpt_Period_Cd`, not both (`validate-config` reports it; the code is used) |
| `Include_Ind` | Default 1 | `1`, `0` | On a row for one run type: `0` switches that run type off for the source |
| `Active_Ind` | Default 1 | `1`, `0` | |

Ad-hoc requests (`ComplianceRequestInTake`, e.g. CMS) need **no** crosswalk row. List an ad-hoc run type here only
when it has a schedule and the source should get its scheduled batches (e.g. IN1 for SNPCC1).

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
VALUES ('CMSUNIV', 'ODAG3', '10', 'DLY,MNT,CMS', DATE '2026-01-01', NULL, '2200', 1, 1);
-- FDR 51: weekly, monthly and ad hoc
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'WKLY,MNT,CMS', DATE '2026-01-01', NULL, '2200', 1, 1);
-- ... but 51's MNT moves to version 2300 from November, with the last 3 months as report dates
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Period_Cd, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'MNT', DATE '2026-11-01', NULL, '2300', 'RANGE(MONTH,3,3)', 1, 1);
-- ESI 140 sends weekly, monthly and ad hoc ...
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'WKLY,MNT,CMS', DATE '2026-01-01', NULL, '2200', 1, 1);
-- ... but stops its weekly batches from December (same as removing WKLY from the list, with a start date)
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'WKLY', DATE '2026-12-01', NULL, '2200', 0, 1);
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
columns you map. Staging is cleared per source and run type before each load (per source and batch without a run-type column, mapped to `RUN_TYPE`): another source's rows stay, so a file and an internal pull of the same table do not remove each other's rows. Staging (e.g. the
`..._FILELAND_...` database) can keep every column as text; core can use real types (`DATE`, `TIMESTAMP(0)`,
`DECIMAL`, `INTEGER` …): the framework converts on the way to core (below).

**How a file goes from the NAS to core** (one FILE_LOAD run, file by file):

| # | Step | Result |
|---|---|---|
| 1 | The name is matched to its file config and its open batch; a `ComplianceFileLoad` row is written | `RECEIVED` |
| 2 | File checks (5.9) | Failed: `REJECTED`, file to `Error/`, error log in `ErrorLog/`, email |
| 3 | The source's earlier staging rows of the run type are removed (of the batch, without a run-type column), the file's records inserted with the derived columns | `STAGED`, `Stg_Rcd_Cnt` |
| 4 | Every NOT NULL core column must get a value; every text staging value whose core column has another type is test-converted to the core column's type and `FORMAT` (from `DBC.ColumnsV`) | A NOT NULL core column with no staging column, a value that does not convert, or an empty value for a NOT NULL column: `REJECTED` (`CORE_TYPE_MISMATCH`, columns named), staged rows removed, core untouched |
| 5 | One transaction: the batch's core rows of the source are removed and the staged rows copied in (converted as in step 4); the copied count must equal the staged count | `FILE_LOADED`, batch `SOURCE_DATA_READY` |
| 6 | The file moves to `Archive/`, a line goes to the control report | |

Conversion rules: an empty value becomes NULL; dates and times use the core column's own `FORMAT` (a DATE column
without one has the system default, often `YY/MM/DD` or `YYYY-MM-DD`, so give it the file's format, e.g.
`DATE FORMAT 'MM/DD/YYYY'`); the load date / time and the report dates are written in each table's own type (a date
in a `TIMESTAMP` column is its midnight); columns of the same kind in both tables are copied as they are. Internal
source pulls (SOURCE_LOAD) follow steps 3 to 5. A database error at any step (a missing table, the database down) is
`PROCESS_FAILURE`: nothing is half done, the file stays in the inbound folder and is tried again on the next run.

**Which staging column fills which core column:** a core column takes the staging column of the same name; a derived column (5.10) the staging column with the same value code. The data columns whose names differ are paired **in table order**: the first such staging column fills the first such core column, and so on (e.g. staging `MemberFirstName, MemberID` → core `EnrolleeFirstName, EnrolleeID`), so keep those columns in the same order in both tables. Core columns left over stay empty (or get the load time with `LOAD_TIMESTAMP`); staging columns left over are not copied. The pairs with different names are written to the task log (`staging -> core by column position: ...`), and `validate-config` writes them too.

### 5.8 ComplianceInternalSourceConfig (internal sources, optional)

One row per internal source that is **pulled with a query** (SOURCE_LOAD) instead of sending a file. Such a source
has no file config.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | | |
| `Src_Db_Ty` | Yes | `TERADATA`, `SQLSERVER` | Where the source table is |
| `Src_Conn_Cd` | No | An Airflow Connection id; `$env` allowed | Empty = the framework's own Teradata system (copied with one INSERT … SELECT); otherwise the source server's Connection |
| `Src_Sql_Txt` | Yes | One `SELECT` in the source's dialect | Its column names fill the staging columns of the same name: every name must be a column of the staging table, else the pull is rejected with the names. A column the query returns wins over its `ComplianceTargetColumnConfig` row for that pull (e.g. an ID from the source instead of `ROW_SEQUENCE`); only the batch (`Btch_ID` / `BATCH_ID`), source (`SOURCE_ID`) and load (`LOAD_ID`) columns are always the framework's. Placeholders (section 8.9) are bound as parameters. `$env` is **not** replaced inside it |
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
data yet" and is tried again on the next runs; empty on or after the close date means "no data", and the batch is
still pulled again on every run until it closes, so a source table loaded later that day is picked up, e.g. a DLY
batch with SLA 1). Ad hoc run types are pulled the same way.

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
| `Check_Field_Nm` | Default `*` | A field name or `*` | For field checks: the field (a `HEADER_NAMES` name, else a staging column). `*` for the other checks. The table's key is project, table, source and `Check_Cd` (not the field), so one field per check for a table + source |
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
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind) VALUES ('CMSUNIV', '*', 'StagingCreateDate', 'LOAD_DATE', NULL, 1);
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
| `Exp_Schema_Nm`, `Exp_Table_Nm` | Yes | Database and table; `$env` allowed | The export table: the core table's columns (row ID included), `Btch_ID` and the source column |
| `Exp_Proc_Nm` | No | `database.procedure`; `$env` allowed | A procedure that does the export instead of the framework (it deletes and inserts that source's rows itself) |
| `Exp_Proc_Parm_Txt` | No | e.g. `{btch_id}, {data_btch_id}, {src_id}` or `'PROVISIONING_WORK_$ENV', {run_ty}, MSG` | The procedure's arguments in order: placeholders, fixed values in quotes and the OUT parameter's name (section 8.10); empty = `{btch_id}, {data_btch_id}` |
| `Exp_Sql_Txt` | No | One `SELECT` | Used when there is no procedure: its rows are the export rows of a batch + source, instead of the plain copy from core. Name each column after an export column (`AS`); the batch column and the source column are always filled by the framework. Placeholders as for the procedure (8.10), e.g. `{data_btch_id}` (the batch holding the data: the batch itself, or the one it carries forward) and `{src_id}` |
| `Exp_Pre_Sql_Txt` | No | One statement: `CALL`, `UPDATE`, `INSERT`, `DELETE`, `MERGE` … | Runs **once per batch**, before its sources are exported |
| `Exp_Post_Sql_Txt` | No | One statement | Runs **once per batch**, after all its sources are exported |
| `Active_Ind` | Default 1 | | |

```sql
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Proc_Nm, Exp_Proc_Parm_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG3_2022', NULL, NULL, 1);
-- an existing feed table: it needs the batch column (Btch_ID) and the source column (e.g. Src_Data_Key)
```

**How a batch is exported**, for each batch + source, in this order of choice:

| Mode | When | What writes the export rows |
|---|---|---|
| Procedure | `Exp_Proc_Nm` set | The procedure (it removes and inserts that source's rows itself) |
| Custom SELECT | `Exp_Sql_Txt` set, no procedure | The framework removes the source's earlier rows of the period, then inserts the SELECT's rows, stamped with the batch and source |
| Copy | Neither | The framework removes the source's earlier rows of the period, then copies the rows from the source's own core table: columns of the same name, the framework's columns by what fills them (batch, source, version …), the rest by column order (a core table with other names, e.g. an internal source's); a text value going into a typed export column is converted; export columns left over stay empty (`validate-config` lists them and the pairs by order) |

**Pre / post statements.** With `Exp_Pre_Sql_Txt` or `Exp_Post_Sql_Txt`, a batch is exported as one transaction:
pre, then every source of the batch (in any mode), then post. If any of them fails, all of it is rolled back, every
source of the batch is reported `FAILED` (`EXPORT_LOAD_FAILED`, emailed) and it is tried again on the next run.
Without them, each source is exported on its own as before. Placeholders of pre / post (batch level): `{btch_id}`,
`{project_cd}`, `{table_nm}`, `{run_ty}`, `{version}`, `{req_dt}`, `{rpt_start}`, `{rpt_end}`,
`{exp_schema_nm}`, `{exp_table_nm}`. All three texts: one statement (no `;` inside; several steps go in a
procedure), placeholders passed as parameters (write `{btch_id}`, not `'{btch_id}'`), `$env` not replaced, every
statement written to the task log with its values; `validate-config` checks them.

```sql
UPDATE HSABC_DEV.ComplianceExportConfig
SET Exp_Sql_Txt = 'SELECT Row_ID, EnrolleeID AS Member_Key, PlanType FROM CMSUNIV_CORE_DEV_T.SNPCC_HS_TABLE1_2022 WHERE Btch_ID = {data_btch_id} AND Src_Data_Key = CAST({src_id} AS SMALLINT)',
    Exp_Pre_Sql_Txt = 'CALL EXPORT_FEED_DEV_T.SP_EXPORT_PREP({btch_id}, {run_ty})',
    Exp_Post_Sql_Txt = 'UPDATE EXPORT_FEED_DEV_T.SDO_CMSUNIV_SNPCC1_2022 SET Export_Dt = CURRENT_DATE WHERE Btch_ID = {btch_id}'
WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'SNPCC1';
```

What the export holds: per source, run type and report period, the latest batch with data (or with "no data" from the
source: an empty file or pull). A later batch of the same dates that closed with nothing (`MISSING`) does not replace
it, so the batch with the data can still be re-exported. A carried-forward batch
gets a copy of the reused rows under its own batch ID.

### 5.12 ComplianceRuleBinding (optional)

Optional: without a row, FILE_RULES runs the rule group named like the table with the batch's run type as variant
(2.12). A row names other rule groups for a table (and run type); rows add up; `Src_ID` is `*` (rules run per
batch, for all its sources).

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

One row per request and source. BATCH_CREATION creates the batch on each day of the request window. A request
stands on its own: it needs **no crosswalk row** (the crosswalk is for scheduled batches); its source and compliance
version are on the row. The source needs an active file config or internal source config for the table, so its
data can arrive.

| Column | Required | Values | Meaning |
|---|---|---|---|
| `Intake_ID` | (framework) | | Generated: leave out |
| `Project_Cd`, `Table_Nm` | Yes | | |
| `Src_ID` | Yes | A source | The source of the batch (one request per source) |
| `Run_Ty` | Yes | Any active run type (ad hoc, or a scheduled one outside its schedule) | |
| `Cmplnc_Vrsn` | Yes | e.g. `2200` | The compliance version stored on the batch and written to the `VERSION` column |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Yes | Dates | Report dates of the batch |
| `Req_Start_Dt_Key`, `Req_End_Dt_Key` | Yes | Dates | The days batches are created (same date twice = one batch) |
| `Last_Run_Dt_Key` | (framework) | | Last day the request was handled: leave out |

A request whose source, run type and report dates already have an **open** batch made by the schedule or another
request uses that batch.

```sql
INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
VALUES ('CMSUNIV', 'ODAG3', '51', 'CMS', '2200', DATE '2026-07-01', DATE '2026-09-30', DATE '2026-10-19', DATE '2026-10-19');
-- the same request for every source of ODAG3 that has a file or internal source config (one row per source)
INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
SELECT 'CMSUNIV', 'ODAG3', Src_ID, 'CMS', '2200', DATE '2026-07-01', DATE '2026-09-30', DATE '2026-10-19', DATE '2026-10-19'
FROM (SELECT Src_ID FROM HSABC_DEV.ComplianceSourceFileConfig WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG3' AND Active_Ind = 1
      UNION SELECT Src_ID FROM HSABC_DEV.ComplianceInternalSourceConfig WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG3' AND Active_Ind = 1) s;
-- a daily ad-hoc batch for the previous day, for every day of November (one row per day)
INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
SELECT 'CMSUNIV', 'ODAG3', '140', 'CMS', '2200', c.calendar_date - 1, c.calendar_date - 1, c.calendar_date, c.calendar_date
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
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DLY' AND Src_ID = '51';
-- LATE_ARRIVAL: a closed batch without data accepts a late file
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'LATE_ARRIVAL', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31', 'ESI file came late', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DLY' AND Src_ID = '140';
-- CORRECTION: a closed batch with data accepts a corrected file
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'CORRECTION', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31', 'Corrected amounts', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DLY' AND Src_ID = '51';
-- REOPEN: open a closed batch again until new data arrives
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'REOPEN', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31', 'Resend expected', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_DLY' AND Src_ID = '140';
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
`Reject_Rsn_Cd` holds the rejection reason (an audit event code), `Error_Txt` the technical detail,
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

| Value | Batches created | Internal sources pulled | Exported | Closed |
|---|---|---|---|---|
| `SCHEDULED` | By the schedule (codes or SQL), the calendar, or an intake request | From the request date (empty before the close date = try again) | From the close date (from `RELEASE_TIME` on that day, if set), or when released | From the close date |
| `ADHOC` | By intake requests (no crosswalk row needed), and by a schedule if the run type has one, for the crosswalk rows that list it and find a report period (e.g. IN1) | As `SCHEDULED` | As `SCHEDULED` | With data: when released (11.6); without data: from the close date |

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
its report dates are the same as the previous one's (a DLY run type with fixed dates, e.g. `ANNUAL_WINDOW`, gets a
batch every day).

### 8.3 SQL fallbacks for schedule and report dates

Only when no code is set. Both are a single `SELECT` (no `;` inside).

- `Batch_Schedule_Sql_Txt`: returns **one row** on a day batches are due, **no row** otherwise. Placeholders:
  `{run_date}`, `{project_cd}`, `{run_ty}`. Its columns can be used by name in `Rpt_Dt_Sql_Txt` (e.g. `period_dt`).
- `Rpt_Dt_Sql_Txt` (on a `ComplianceRunTypePeriod` row, or on a crosswalk row): returns one row with `rpt_start` and
  `rpt_end`. Placeholders: `{run_date}`, `{project_cd}`,
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
| `HEADER_NAMES` | `*` | `MemberFirstName\|MemberID\|Amount` | The header line's names in order (case and spaces ignored); also decides which column each field loads to: the column of the same name when every name is one, otherwise by position (validate-config warns `HEADER_NAMES_PARTIAL` when only some names are columns) |
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
| `FILE_NAME` | The file name (pulls: `<project>_<table>_<source>_<pull start YYYYMMDDHHMISS>`, e.g. `CMSUNIV_ODAG3_10_20261027143005`) |
| `LOAD_TIMESTAMP` | Load time (timestamp, or text `YYYY-MM-DD HH:MM:SS` in a character column): taken once when the load (file or pull) starts, so every row of it, in staging, core and the export, has the same value; `LOAD_DATE` is its date |
| `LOAD_DATE` | Load date (date, or text `YYYY-MM-DD`) |
| `ROW_SEQUENCE` | Running row number, continuing from the highest in staging and core (row ID) |
| `LOAD_ID`, `REQ_ID` | The load number, the batch row number |
| `PROJECT`, `TABLE` | Project and table codes |
| `RPT_START`, `RPT_END` | Report dates |
| `CURRENT_FLAG` | Core only: always 1 |
| `END_TIMESTAMP` | Core only: always empty |
| `CONSTANT` | `Value_Txt` on every row |
| `SQL` | The one value of the `SELECT` in `Value_Txt` (placeholders section 8.9) |

The same column can have different types in staging and core. `LOAD_DATE`, `LOAD_TIMESTAMP`, `RPT_START` and `RPT_END` are written to each table in that table's type (a date in a timestamp column is its midnight). Any other text staging column whose core column is a number, date, time or timestamp is converted to the core column's type as `DBC.ColumnsV` gives it, dates and times with the column's own `FORMAT` (e.g. `DATE FORMAT 'MM/DD/YYYY'`); an empty value becomes NULL. If a value does not convert (or a NOT NULL core column would be empty), the file or pull is rejected (`CORE_TYPE_MISMATCH`, the column named, no data shown), its staged rows are removed and core is not touched. Columns of the same kind in both tables are copied as they are.

### 8.8 Override types (`ComplianceBatchOverride.Override_Ty`)

| Type | For | Effect | Applied by |
|---|---|---|---|
| `REUSE` | An open batch without data (nothing yet, or an empty file / pull), run type with `Carry_Fwd_Ind = 1` | Carries the data of an earlier closed batch (same table, source, run type) forward; closes as `REQUEST_COMPLETE` / `CARRY_FORWARD`. Must be applied before the batch's close run | OVERRIDE_DECISIONS |
| `LATE_ARRIVAL` | A closed batch without data (`DATA_NOT_PROVIDED`: nothing came, or an empty file / pull) | A late file is accepted; the batch stays closed, becomes `REQUEST_COMPLETE` | FILE_LOAD |
| `CORRECTION` | A closed batch with data (`REQUEST_COMPLETE`: a file / pull with rows, or carried forward) | A corrected file replaces the data; the batch stays closed | FILE_LOAD |
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
reuses, or NULL when no data), `{reused_btch_ids}` (batches it reuses, comma
separated: keep their rows), `{project_cd}`, `{table_nm}`, `{src_id}`, `{run_ty}`, `{version}`, `{req_id}`,
`{req_dt}`, `{rpt_start}`, `{rpt_end}`. The procedure deletes and inserts only that source's rows and fills the source
column.

Besides placeholders, `Exp_Proc_Parm_Txt` takes, in any position:

| Item | Example | Passed as |
|---|---|---|
| Fixed value, in quotes (a quote inside doubled) | `'EXPORT_FEED_$ENV_T'`, `'(''1''=''1'')'` | The text between the quotes (`''` is one quote), as a parameter. Only a value with `$ENV` changes, and only at the token: it is replaced at runtime (`EXPORT_FEED_DEV_T` in DEV; in UAT / PROD, where it is empty, the underscore next to it goes too: `EXPORT_FEED_T`), so one row serves every environment. Every other value, e.g. the filter, is passed as written |
| OUT parameter, its name without quotes or braces | `MSG` | Written into the `CALL`; its value is written to the task log and the step report (`procedure_output`) |

```sql
UPDATE HSABC_DEV.ComplianceExportConfig
SET Exp_Proc_Nm = 'HSPROCS_$ENV.sp_OSS_DA_PROV_TO_EXPORT_LOAD_CMSUNIV',
    Exp_Proc_Parm_Txt = '''PROVISIONING_WORK_$ENV'', ''CMSUNIV_CDAG_Table1_2022'', ''EXPORT_FEED_$ENV_T'', ''SDO_CMSUNIV_CDAG1_2022'', ''CDAG1'', {run_ty}, {version}, ''(''''1''''=''''1'''')'', MSG'
WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG1';
-- EXPORT_LOAD in DEV calls: CALL HSPROCS_DEV.sp_OSS_DA_PROV_TO_EXPORT_LOAD_CMSUNIV('PROVISIONING_WORK_DEV', 'CMSUNIV_CDAG_Table1_2022',
--   'EXPORT_FEED_DEV_T', 'SDO_CMSUNIV_CDAG1_2022', 'CDAG1', <run type>, <version>, '(''1''=''1'')', MSG)
```

A name without braces that is a placeholder (e.g. `btch_id`) is still the placeholder; any other name is an OUT
parameter, so a fixed value needs its quotes (`validate-config` reports a name that is not one word).

### 8.11 Batch statuses (`ComplianceRequestControl.Req_Stat`)

| Status | Open / closed | Meaning |
|---|---|---|
| `REQUEST_CREATED` | Open | Created; no data yet |
| `SOURCE_DATA_READY` | Open | Data loaded / pulled |
| `SOURCE_DATA_READY_NO_DATA` | Open | The source delivered "no data" (empty file, or empty pull on/after the close date) |
| `SOURCE_DATA_CARRY_FORWARD` | Open | Data carried forward (REUSE) |
| `REQUEST_COMPLETE` | Closed | Closed with data (`NEW_FILE` or `CARRY_FORWARD`) |
| `DATA_NOT_PROVIDED` | Closed | Closed without data (`NO_DATA` or `MISSING`). Final, like `REQUEST_COMPLETE` |

### 8.12 Resolution types (`Resolution_Ty`)

`NEW_FILE` (own data), `NO_DATA` (the source said no data), `CARRY_FORWARD` (reused data), `MISSING` (nothing by the
close date).

### 8.13 Load and rules statuses (`ComplianceFileLoad`)

| `Load_Stat` | Meaning | Report status |
|---|---|---|
| `RECEIVED`, `STAGING`, `STAGED` | In progress | – |
| `FILE_LOADED` | Loaded into core: the batch's current data (files and internal source pulls) | ACCEPTED |
| `RELOADED` | Was loaded, replaced by a newer load (or a carry-forward) | ACCEPTED |
| `REJECTED` | Rejected (`Reject_Rsn_Cd` says why); file moved to `Error` | REJECTED |
| `PROCESS_FAILURE` | System problem; tried again next run | FAILED |
| `NO_DATA_YET` | Internal source had no rows before the close date; tried again | WAITING |
| `LOAD_INCOMPLETE` | Another run was loading into the same staging table (`Reject_Rsn_Cd = STAGING_TABLE_BUSY`); the next run picks the file / pull up (no error) | WAITING |

| `Rules_Stat` | Meaning |
|---|---|
| `NOT_RUN` | Rules not run yet (FILE_RULES waits for the export when the table has one) |
| `PASSED` / `PASSED_WITH_WARNINGS` | All rules passed (warnings under `FILE_RULES_MODE = ANNOTATE`) |
| `FAILED` | A rule failed (reported, emailed; nothing else changes) |
| `SKIPPED` | No active rules for the batch's table / run type, or the batch has no data rows |
| `ERROR` | The engine could not run; tried again |

### 8.14 Timeline events (`ComplianceRequestFileDetail.Event_Ty`)

| Event | When |
|---|---|
| `BATCH_CREATED` | The batch row was created |
| `FILE_RECEIVED` | A file / pull reached the batch |
| `FILE_LOADED` | Data loaded into core |
| `LATE_ARRIVAL_LOADED`, `CORRECTION_LOADED` | Data loaded into a closed batch with an override |
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
| `FILE_MOVE_FAILED` | WARNING | yes | File could not be moved to Archive / Error; it stays in the inbound folder and the next runs only try the move (no second load) |
| `STAGING_LOCK_STALE` | WARNING | yes | A staging table has been locked for 30 minutes or more (a crashed run): its files and pulls wait. Free it with `release-lock` (2.11) if that run no longer exists |
| `FILE_TECHNICAL_FAILURE` | ERROR | yes | System problem loading a file |
| `CORE_LOAD_ROWCOUNT_MISMATCH` | ERROR | yes | Core rows differ from staged rows |
| `SOURCE_QUERY_FAILED`, `SOURCE_DATA_REJECTED` | ERROR | yes | Internal source query failed / data does not fit |
| `CORE_TYPE_MISMATCH` | ERROR | yes | A file or pull has a value that does not convert to its core column's type; rejected |
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
| NAS (`comp_oper_creds`, or the name in `wdc_comp_oper_nas_var`) | host, login, password, port (445) | FILE_CHECK, FILE_LOAD |
| SQL Server source (name = `Src_Conn_Cd`) | host or `server\instance`, port, schema = database, login, password; Extra `{"driver": "ODBC Driver 17 for SQL Server", "encrypt": "yes", "trust_server_certificate": "yes"}` (`driver` is optional: without it the newest `ODBC Driver NN for SQL Server` installed on the worker is used, and the log names it) | SOURCE_LOAD |
| `smtp_default` | Airflow SMTP connection | DAG emails, NOTIFY (`NOTIFY_BACKEND = airflow`) |

### 9.2 DAGs

One DAG file per step in `airflow/` (`osstd_core_cms_compliance_<step>.py`); another project copies them with its
own DAG ids and Variable names.

| Step | DAG id | Variable |
|---|---|---|
| BATCH_CREATION | `OSSTD_CORE_CMS_COMPLIANCE_BATCH_CREATION` | `compliance_batch_creation_config_var` |
| FILE_CHECK | `OSSTD_CORE_CMS_COMPLIANCE_FILE_CHECK` | `compliance_file_check_config_var` |
| FILE_LOAD | `OSSTD_CORE_CMS_COMPLIANCE_FILE_LOAD` | `compliance_file_load_config_var` |
| SOURCE_LOAD | `OSSTD_CORE_CMS_COMPLIANCE_SOURCE_LOAD` | `compliance_source_load_config_var` |
| EXPORT_LOAD | `OSSTD_CORE_CMS_COMPLIANCE_EXPORT_LOAD` | `compliance_export_load_config_var` |
| OVERRIDE_DECISIONS | `OSSTD_CORE_CMS_COMPLIANCE_OVERRIDE_DECISIONS` | `compliance_override_decisions_config_var` |
| BATCH_CLOSE | `OSSTD_CORE_CMS_COMPLIANCE_BATCH_CLOSE` | `compliance_batch_close_config_var` |
| NOTIFY | `OSSTD_CORE_CMS_COMPLIANCE_NOTIFY` | `compliance_notify_config_var` |
| FILE_RULES | `OSSTD_CORE_CMS_COMPLIANCE_FILE_RULES` | `compliance_file_rules_config_var` |

A DAG never runs twice at the same time; a run that is due while another runs waits.

### 9.3 Variable keys

Every DAG has one Variable (JSON object; keys in any case). Put in a Variable **only what differs from the default**:
the framework fills in everything else. A CMSUNIV DEV Variable (`airflow_resources/examples/cmsuniv_dev`):

```json
{"td_conn_var": "DEV", "meta_db": "HSABC_DEV", "load_env": "DEV",
 "email_recipient": "TRINATH_gundavaram@example.com", "project": "CMSUNIV",
 "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_EXPORT_LOAD", "OSSTD_CORE_CMS_COMPLIANCE_OVERRIDE_DECISIONS"]}
```

**Connection and environment**

| Key | Required | Default | Values |
|---|---|---|---|
| `meta_db` | **Yes** | | The metadata database, e.g. `HSABC_DEV`, `HSABC_PROD` |
| `td_conn_var` | No | `compliance_teradata` | The Airflow Connection id of Teradata (CMSUNIV: `DEV`) |
| `wdc_comp_oper_nas_var` | No | `comp_oper_creds` | The Airflow Connection id of the NAS; used by FILE_CHECK and FILE_LOAD only |
| `load_env` | No | `DEV` | `DEV`, `TEST`, `QA`, `UAT`, `PROD` (any letters / digits): the value of `$env` in database names and paths (uppercase where it stands in upper case) and the email subjects |
| `email_recipient` | No | none | One address, a list `["a@x.com", "b@x.com"]` or `"a@x.com,b@x.com"`. Gets the DAG's success / failure emails and, in the NOTIFY Variable, the alert emails (9.4 `DEFAULT_NOTIFY_EMAILS`). Without it: no emails |

**What the run works on** (also accepted from a trigger, 9.5)

| Key | Steps | Default | Values |
|---|---|---|---|
| `project` | all | every project | A `Project_Cd`, e.g. `CMSUNIV`. Leave it out only in a NOTIFY that should send the events of every project |
| `table_group` | all | all | One or more `Table_Grp_Cd` of `ComplianceTable`: `"ODAG"`, `"ODAG,FA"` or `["ODAG", "FA"]` (every active table of those groups) |
| `table` | all | all | One or more `Table_Nm`: `"ODAG3"`, `"ODAG3,FA4"` or `["ODAG3", "FA4"]`. Give table groups or tables, not both |
| `run_type` | all | all | A `Run_Ty`, e.g. `DLY`, `WKLY`, `MNT` |
| `source` | all | all | One or more sources, by `Src_ID` or `Src_Nm` of `ComplianceSourceSystem`: `"QNXT"`, `"10,11"` or `["10", "DatamartSNPMOC"]` (a name stands for every source ID with that name). Only those sources' batches, files and events; an unknown source stops the run |
| `period` | BATCH_CREATION | from the schedule codes | A period code (8.4), e.g. `PREV_DAY`: create batches with these report dates now |
| `period_file` | BATCH_CREATION | none | A Python file with a custom period function (advanced) |
| `lookback_days`, `lookback_weeks` | BATCH_CREATION | none | Whole numbers: create the missed batches of the last n days / weeks |
| `batch` | SOURCE_LOAD, EXPORT_LOAD | all due | One `Btch_ID` |
| `release` | SOURCE_LOAD, EXPORT_LOAD, BATCH_CLOSE | `false` | `true`: release the scope's batches now: held / not yet due batches are pulled and exported; batches with data close on BATCH_CLOSE (11.6) |
| `hold` | SOURCE_LOAD, EXPORT_LOAD, BATCH_CLOSE | none | Run types to leave alone, e.g. `"WKLY"` or `["WKLY", "MNT"]` |
| `as_of` | all | now | A date or timestamp, `2026-10-18` or `2026-10-18T06:00`: run as if it were then (Central time) |
| `share`, `file`, `folder` | FILE_LOAD (trigger) | all files | Load one file / folder |

**Schedule and chaining**

| Key | Default | Values |
|---|---|---|
| `schedule_interval` | none (runs only when triggered) | A cron expression in `schedule_timezone`, e.g. `0 6 * * *` (06:00 daily), `*/15 * * * *` (every 15 minutes) |
| `schedule_timezone` | `America/Chicago` | An IANA time zone name |
| `trigger_dags` | none | DAG ids started after the step succeeds (FILE_CHECK: also when it failed but at least one file passed): a list, or one comma-separated string. Use the project's own DAG ids |
| `runs` | none | A list of scopes, one task each; with `name` and `schedule_interval` per entry they are timed runs (9.6) |

**Task behaviour**

| Key | Default | Values |
|---|---|---|
| `retries` | `1` | `0` or more: how often a task that hit a technical error is tried again (5 minutes later). Problems the step reports (a rejected file) fail at once |
| `fail_on_problems` | `true` (OVERRIDE_DECISIONS `false`) | `true` / `false`: fail the task when the step reports problems |
| `execution_timeout_minutes` | BATCH_CREATION, FILE_CHECK 60; FILE_LOAD, SOURCE_LOAD, EXPORT_LOAD 120; OVERRIDE_DECISIONS, BATCH_CLOSE, NOTIFY 30 | Minutes. `LOCK_TTL_MINUTES` is raised to this + 30 when lower |
| `dagrun_timeout_minutes` | `480` | Minutes |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `owner` | `oss` | Airflow owner |
| `tags` | `Compliance`, `Compliance_Batch_Framework` | A list (the step's name is always added) |
| `snow_assignment_group` | `D&AE - EDE Govt Compliance` | ServiceNow group of the incident opened when a task fails |
| `settings` | none | Framework settings that differ from their default (9.4) |
| `pool` | none | An Airflow pool for the step's task (FILE_RULES: `gre_rules`, 1 slot, created under Admin → Pools) |
| `wait_for_triggered` | `false` | `true`: the `trigger_dags` tasks wait until the started DAG ends (deferred: no worker slot held; needs the Airflow triggerer) and fail if it fails. EXPORT_LOAD: `true` |
| `gre` | from the Variable | GRE rules engine settings (FILE_RULES only), all optional: `batch_column` (`Btch_ID`: the batch column of the rules' tables / views), `meta_db` (the GRE tables' database, default `meta_db`), `environment` (`load_env`, for `$env` in rules), `project_name` / `process_name` (the project), `log_level`, `max_parallel_rules` (1), `package_dir` (`<dags>/rules_engine`), `run_params` / `text_params` / `extra_filters` (added to the framework's) |

**Success / failure emails and NOTIFY are different.** The success / failure emails (`email_recipient`) say whether
the DAG run worked. NOTIFY emails the **business alerts** the steps record in `CMS_ComplianceExceptionsAudit`: which
file was rejected and why, a source missing at the close date, a carry-forward applied, a file that could not be
moved, a stuck staging lock, a failed export (8.15). Many of these do not fail the DAG (the run itself worked), so
only NOTIFY tells you about them. NOTIFY sends every alert not sent yet, then marks it sent: run it after
BATCH_CLOSE (its `trigger_dags`) and, when the DAGs run unattended, on its own schedule (e.g. `0 * * * *`), so the
alerts of the day's loads arrive within the hour. It does not need to follow every step.

### 9.4 Framework settings (`"settings": {...}`)

All have a default; set one only to change it. The names are not case sensitive. Under Airflow the email settings
come from the Variable: `NOTIFY_BACKEND = airflow`, `DEFAULT_NOTIFY_EMAILS` = `email_recipient`, `NOTIFY_FROM_EMAIL`
= its first address.

| Setting | Default | Values and meaning |
|---|---|---|
| `BUSINESS_TZ` | `America/Chicago` | IANA time zone of the business day (dates, close dates, timestamps written) |
| `FILE_STORE` | `nas` | `nas` (the NAS over SMB) or `local` (a local folder, tests) |
| `LOCAL_STORE_ROOT` | `./.local_store` | Folder of `FILE_STORE = local` |
| `ARCHIVE_FOLDER` | `Archive` | Subfolder of the inbound folder for loaded files |
| `ERROR_FOLDER` | `Error` | Subfolder for rejected files |
| `ERROR_LOG_FOLDER` | `ErrorLog` | Subfolder for the error logs of rejected files |
| `CONTROL_REPORT_FOLDER` | `ControlReport` | Subfolder for the control reports |
| `ARCHIVE_BY_YEAR` | `false` | `true`: archive into `Archive/<year>/` |
| `NAS_MIN_AGE_SECONDS` | `120` | Seconds: a file modified more recently is left for the next run (still arriving) |
| `SUPPORTED_FILE_TYPES` | `.txt,.csv` | Comma-separated extensions; other files are rejected |
| `FILE_ENCODING` | `utf-8` | `utf-8`, `latin-1`, `cp1252`, … |
| `QUOTE_CHAR` | `"` | One character, or empty for none |
| `EMPTY_AS_NULL` | `true` | `true`: an empty field is NULL; `false`: an empty string |
| `CHECK_CONTENT` | `true` | FILE_CHECK reads the content; `false`: name and batch only |
| `CHECK_MOVE_FAILED` | `false` | FILE_CHECK moves a file that fails its checks to `Error/` (CMSUNIV: `true` in the FILE_CHECK Variable) |
| `CHECK_ON_LOAD` | `false` | `true`: FILE_LOAD runs the content checks itself before loading |
| `WRITE_ERROR_FILE` | `true` | Write an error log for a rejected file |
| `WRITE_CONTROL_REPORT` | `true` | Write the control report of each FILE_LOAD run |
| `LOAD_ENGINE` | `BATCH` | `BATCH` (inserts in batches) or `FASTLOAD` (very large files; test first) |
| `INSERT_BATCH_ROWS`, `INSERT_BATCH_BYTES` | `2000`, `500000` | Rows / bytes per insert batch |
| `RULE_ENGINE` | `gre` | `gre` or `none` (no data quality rules) |
| `GRE_ENTRYPOINT` | `compliance_framework.export:run_gre` | The function that runs a batch's rules (the framework's GRE bridge) |
| `FILE_RULES_MODE` | `GATE` | `GATE` (a failed rule = `FAILED`) or `ANNOTATE` (= `PASSED_WITH_WARNINGS`) |
| `RELEASE_TIME` | none | `HH:MM`: on the close date, export (or pull) only from that time |
| `LOCK_TIMEOUT_SECONDS` | `300` | Seconds a step waits for a busy batch |
| `LOCK_TTL_MINUTES` | `240` | Minutes after which a lock left by a crashed run expires |
| `HEARTBEAT_STALE_MINUTES` | `30` | Minutes after which `health` lists a load still in progress as stuck |
| `NOTIFY_BACKEND` | `airflow` (command line: `log`) | `airflow` (email through `smtp_default`) or `log` (write the alert to the log only) |
| `DEFAULT_NOTIFY_EMAILS` | `email_recipient` | Alert recipients, comma-separated |
| `NOTIFY_FROM_EMAIL` | first `email_recipient` | Sender address of the alerts |
| `ENV_VALUE` | from `load_env` | Value of `$env` when it should differ from `load_env`. `$env` in a name is `dev` / `qa` in DEV / QA and empty in UAT / PROD; an empty one leaves no extra underscore (`EXPORT_FEED_$ENV_T` → `EXPORT_FEED_T`, `HSPROCS_$ENV.proc` → `HSPROCS.proc`, `PROVISIONING_WORK_$ENV` → `PROVISIONING_WORK`) |

### 9.5 Triggering a DAG by hand (JSON)

These keys override the Variable for that run. The database, the connections, `period_file` and the settings
`RULE_ENGINE`, `GRE_ENTRYPOINT`, `FILE_STORE`, `LOCAL_STORE_ROOT`, `METADATA_SCHEMA`, `ENVIRONMENT`, `ENV_VALUE` cannot
be changed from a trigger (ignored, with a warning in the task log):

| Key | Steps | Meaning |
|---|---|---|
| `project`, `table_group`, `table`, `run_type`, `source` | all | Scope |
| `batch` | SOURCE_LOAD, EXPORT_LOAD | One or more `Btch_ID`s, comma separated (every source of the batch) |
| `release` | SOURCE_LOAD, EXPORT_LOAD, BATCH_CLOSE | `true` = release the scope's batches now (11.6) |
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
| `python -m compliance_framework.cli run --module <STEP> --project CMSUNIV [--source 10,11] [--as-of <date>]` | Runs any step by hand, also `FILE_RULES` |

### 9.6 One DAG at several times (timed runs)

```json
{"meta_db": "HSABC_DEV", "td_conn_var": "DEV", "load_env": "DEV", "project": "CMSUNIV", "schedule_interval": null,
 "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_NOTIFY"],
 "runs": [{"name": "DLY", "run_type": "DLY", "schedule_interval": "0 20 * * *", "settings": {"RELEASE_TIME": "20:00"}},
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
   core's columns (with `Btch_ID` and the source column).
5. **How data arrives**: a file config per file source (5.7), a query config per internal source (5.8).
6. **Derived columns**: section 5.10 – at least `SOURCE_ID`; usually `RUN_TYPE`, `VERSION`, `FILE_NAME`,
   `LOAD_TIMESTAMP`, `ROW_SEQUENCE`.
7. **File checks**: section 5.9 – header names, record types / trailer, value checks.
8. **Optional**: export (5.11), rules (5.12), calendar (5.6a).
9. **Airflow**: Connections (9.1); the GRE code in `<dags>/rules_engine` and the pool `gre_rules` (1 slot) for
   FILE_RULES; copy the 9 DAG files with project names (e.g. `OSSTD_CORE_PRJB_COMPLIANCE_*`,
   Variables `prjb_compliance_<step>_config_var`) and create their Variables (9.3): every Variable sets `project`;
   `trigger_dags` names the project's own DAGs; NOTIFY's `email_recipient` gets the alerts. Keep one NOTIFY without
   `project` for events of no project.
10. **Validate**: `validate-config` from the command line (2.11); fix every ERROR.
11. **First cycle in DEV**: BATCH_CREATION → put a test file / source rows → FILE_LOAD / SOURCE_LOAD → EXPORT_LOAD
    → BATCH_CLOSE → NOTIFY; check with section 12. Try a bad file, a missing source, a late file.
12. **Production**: same rows in the PROD metadata DB (`$env` makes database names and paths follow).

---

## 11. Operating procedures (how to …)

**11.1 Check the configuration.** `python -m compliance_framework.cli validate-config` (2.11). ERRORs must be fixed; WARNINGs explain
something to look at.

**11.2 Create batches for a missed day.** Trigger BATCH_CREATION with `{"project": "CMSUNIV", "as_of": "2026-10-19"}`.

**11.3 Request an ad-hoc batch.** Insert into `ComplianceRequestInTake` (6.1); the next BATCH_CREATION creates it.
Ad-hoc batches are pulled and exported like scheduled ones; one with data closes only when released (11.6).

**11.4 A file was rejected.** Find the reason (12.3); the file is in `<inbound>\Error\` and its error log in
`<inbound>\ErrorLog\<name>_errors.txt`. Ask the source to fix and resend, or fix the configuration, then put the file
back into the inbound folder (any name collision gets `_1`, `_2`).

**11.5 A source will not send (carry-forward).** Before the batch's close run: insert an approved `REUSE` override
(6.2); OVERRIDE_DECISIONS applies it. The run type needs `Carry_Fwd_Ind = 1`.

**11.6 Release early / close ad hoc.** Ad hoc batches (IN1, CMS) are pulled and exported like scheduled ones and can
be re-run while open; one with data closes only after a release. To close them: trigger BATCH_CLOSE with
`{"project": "CMSUNIV", "run_type": "IN1", "release": "true"}` (add `"source"` / `"table"` to narrow it); they are
released and closed in that run. To export a scheduled batch before its close date: trigger EXPORT_LOAD (and
SOURCE_LOAD for internal sources) with `{"project": "CMSUNIV", "run_type": "WKLY", "release": "true"}` (or
`"batch": "<Btch_ID>"`); released batches with data close on the next BATCH_CLOSE.

**11.7 Hold a run type for business reasons.** Add `"hold": "WKLY"` to the SOURCE_LOAD, EXPORT_LOAD and BATCH_CLOSE
Variables. Remove it to go back to normal. Release single batches with 11.6 meanwhile.

**11.8 A late file for a closed batch.** Without data: approved `LATE_ARRIVAL`; with data: approved `CORRECTION`.
Then put the file in the inbound folder. Or reopen (11.9).

**11.9 Reopen a closed batch.** Approved `REOPEN` override (6.2); the next OVERRIDE_DECISIONS reopens it; it closes
after new data, or with `close-batch`.

**11.10 Re-pull an internal source.** Trigger SOURCE_LOAD with `{"batch": "<Btch_ID>"}` (replaces the rows; a closed
batch needs its override).

**11.11 Re-export a batch.** Trigger EXPORT_LOAD with `{"batch": "<Btch_ID>"}`.

**11.12 Close a batch by hand.** `close-batch` from the command line (2.11); every open source of the batch, or `--src-id` for
one. Refused before the close date unless released.

**11.13 Run the step for some table groups / tables / a run type / some sources.** Trigger with `table_group` or
`table`, and/or `run_type`, and/or `source`; table groups, tables and sources take one value or a list, e.g.
`{"project": "CMSUNIV", "table_group": "ODAG,FA"}` (one task for both; `"runs": [{"table_group": "ODAG"},
{"table_group": "FA"}]` gives one task each). A table runs all its sources; `source` picks sources across tables, e.g. only
QNXT (10) and SNPCC (11, whose `Src_Nm` is `DatamartSNPMOC`) in SOURCE_LOAD (not AZ or Facets of the same tables):
`{"project": "CMSUNIV", "source": "10,11"}`. In a Variable, `"source": ["10", "11"]`, or one task per source with
`"runs": [{"source": "10"}, {"source": "11"}]`. A trigger's scope is passed on to the DAGs it starts; a Variable's is not, so put the same
`source` in the next DAG's Variable (e.g. EXPORT_LOAD) when it should also work on those sources only.

**11.14 An alert email arrived.** Look up the event in 8.15 and the batch in 12.1 / 12.2.

**11.15 A step waits or says "locked".** `locks` from the command line (2.11); if the owner run no longer exists:
`release-lock --key <Lock_Key>`.

**11.16 Change a version.** For every run type of the source: end the crosswalk row (`Effective_End_Dt_Key`) and
add a new row with the new `Cmplnc_Vrsn` from the next day. For one run type only: add a row for that run type with
the new version from its start date (it replaces the list's entry). Update `File_Vrsn_Cd` if file names change.
For a yearly protocol change with new tables see 11.20.

**11.17 Stop a source or table.** Set `Active_Ind = 0` on its crosswalk rows (and file / query config). To stop
one run type of a source: remove it from the row's list, or add a row for that run type with `Include_Ind = 0`.

**11.18 Add a run type to a source.** Add it to the `Run_Ty` list of the source's crosswalk row (and a
`ComplianceRunTypePeriod` row if the run type is new for the project). Run validate-config.

**11.19 Change the report dates of a run type.** Update `Rpt_Period_Cd` of its `ComplianceRunTypePeriod` row (the
project's `*` row, or add a row for one table). Batches already created keep their dates.

**11.20 Yearly protocol version change (e.g. CMSUNIV 2200 → 2300).** The project and its table names (`Table_Nm`)
stay. Only the tables the new protocol changes move to the new version; the others keep the old version and are
not touched. A changed table gets new staging, core and export tables and a new file version, named like the old
ones with the year changed (`ODAG_DE_TABLE1_2022` → `ODAG_DE_TABLE1_2023`); a query source reads new source tables
(`Staging.cms2022.ODAG_Table3_2022` → `Staging.cms2023.ODAG_Table3_2023`). Old-version files do not arrive after
the cutover. Use `airflow_resources/sql/version_cutover_template.sql`:

| Step | When | What |
|---|---|---|
| Fill the placeholders | – | `{{PROJECT}}`, `{{TABLES}}` (the changed tables), old / new compliance and file version, `{{OLD_TAG}}` / `{{NEW_TAG}}` (the year in the names), `{{CUTOVER_DT}}`. Search for `{{` before running |
| DBA | Before Part 1 | Create the new staging, core and export tables |
| Part 0 | First | Preview: the new table, column and source names, the new queries, the crosswalk rows that move, and the file name templates without `{VERSION}`. Changes nothing |
| Part 1 | Any day before the cutover | Crosswalk rows with the new version from `{{CUTOVER_DT}}` (the old ones end the day before); file configs of the new file version on the new tables; the new table-named columns (`..._2023_ID`, `..._2023_KEY`). Safe to rerun |
| Part 2 | Cutover day, after the last old-version files are loaded and their batches exported | One transaction: old file configs off (new ones on); query sources to the new staging / core tables and source tables; export to the new export table (and the table names in the export procedure's parameters, e.g. the provisioning table). Uncomment 2.2b / 2.4 only where a query's columns or a file check (header names) change |
| Part 3 | After Part 2 | Check the file, query and export configs and the crosswalk windows |
| Validate | After Part 2 | `validate-config` (11.1) |

`{{CUTOVER_DT}}` is the first batch creation date of the new version: a batch takes the version effective on the
day it is created, not on its report period (a December monthly batch created in January gets the new version).
Old batches keep the old version and stay in the old tables; anything reading core directly picks the table by
year. The template replaces the year everywhere in a name or query; check Part 0 for text that holds it otherwise
(a date literal, a filter value) and correct that row by hand.

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
WHERE c.Btch_ID = '20261019_CMSUNIV_ODAG3_DLY'
ORDER BY c.Src_ID, d.Detail_ID;
```

**12.3 Rejected or failed loads (last 7 days)**

```sql
SELECT Load_ID, File_Path, Btch_ID, Load_Stat, Reject_Rsn_Cd, Error_Txt, LastUpdt_Dtts
FROM HSABC_DEV.ComplianceFileLoad
WHERE Load_Stat IN ('REJECTED', 'RULES_FAILED', 'PROCESS_FAILURE') AND LastUpdt_Dtts >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
ORDER BY LastUpdt_Dtts DESC;
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

Every configured statement is written to the task log as it runs: internal source queries (`Src_Sql_Txt`), `SQL` columns (`Value_Txt`), `Rpt_Dt_Sql_Txt`, `Batch_Schedule_Sql_Txt` and export procedure calls. Each entry shows the SQL as sent (`?` = a parameter), every parameter with its value, and the statement with the values written in, which can be copied and run by hand on the same database:

```
INFO - SOURCE_LOAD | 20261020_CMSUNIV_SNPCC1_DLY source 11 | Src_Sql_Txt on SQL_SNPCC
    SQL:         SELECT ... FROM QUALITY.SNPMOC.Table1_cms2022 WHERE BatchID = ?
    parameters:  btch_id = '20261020_CMSUNIV_SNPCC1_DLY'
    with values: SELECT ... FROM QUALITY.SNPMOC.Table1_cms2022 WHERE BatchID = '20261020_CMSUNIV_SNPCC1_DLY'
```

| Symptom | Likely cause | What to do |
|---|---|---|
| File rejected `FILE_REJECTED_UNPARSEABLE` | Name does not match `Src_File_Nm_Tmplt` | Compare the name with the template (8.5); fix the template or ask for the right name |
| `FILE_REJECTED_NO_BATCH` | No batch for that run type / report dates | Check the crosswalk and schedule; create with `as_of` / `period` or an intake request |
| `FILE_REJECTED_BATCH_CLOSED` | Batch already closed | `LATE_ARRIVAL` / `CORRECTION` / `REOPEN` override, then resend |
| Version error in the name | `{VERSION}` ≠ `File_Vrsn_Cd` | Fix `File_Vrsn_Cd` or the file name |
| Column count / header problems | File layout changed | Check `HEADER_NAMES` / `COLUMN_COUNT` / `TARGET_COLUMNS` |
| File rejected `CORE_TYPE_MISMATCH` | A value does not convert to its core column's type or `FORMAT` (the message names the column and type), or a NOT NULL core column is empty | Fix the file, or the core column's type / `FORMAT` (5.7), then resend |
| Load `PROCESS_FAILURE`, "Object '…' does not exist" (error 3807) | A framework table (`sql/schema.sql`) or a staging / core table is missing, or the user has no rights | Create the table / grant; the next run loads the file |
| File rejected `FILE_PARSE_ERROR` "line n" with "Invalid date" / "Invalid timestamp" | A typed **staging** column cannot take the file's value (staging is not converted) | Make the staging column text (5.7), or fix the file; the error log shows the record |
| Load fails "must return one row" (SQL column) | `{btch_id}` lookup returns one row per source | Add `AND Src_ID = {src_id}` or use `{req_id}` |
| "needs a column mapped to SOURCE_ID" | Staging / core / export table without source column | Add the column and a `SOURCE_ID` row (5.10) |
| Batch not exported | Not due yet, or held | Step report `held` list; release (11.6) or wait |
| Ad hoc batch with data stays open | Not released | Release on BATCH_CLOSE (11.6) |
| Batch closed as `DATA_NOT_PROVIDED` / `MISSING` | Nothing by the close date | Expected; late data: `LATE_ARRIVAL` or `REOPEN` |
| Carry-forward refused | Batch closed, run type without `Carry_Fwd_Ind`, no earlier batch with data | Apply before the close run; check 12.7 and the `OVERRIDE_INVALID_DETECTED` text |
| Internal source `WAITING` | No rows yet before the close date | Normal; tried again each run |
| Pull rejected `SOURCE_QUERY_FAILED` "could not connect to …" | Worker cannot reach the source: `[01000] … Can't open lib 'ODBC Driver 18 …'` = that driver is not installed on the worker (list them with `odbcinst -q -d`; set the installed one as `driver` in the Connection's Extra, or leave `driver` out); `[08001]` / `[HYT00]` = server, port, firewall or certificate (`trust_server_certificate`); `[28000]` = login | Fix the worker or the Connection; the next run pulls again |
| Pull rejected `SOURCE_QUERY_FAILED` "the query on … failed" | The `Src_Sql_Txt` fails on the source (`Invalid object name` = table / database name; the Connection has no database unless its Schema is set) | Run the query on the source with the batch's values; fix `Src_Sql_Txt` |
| Step "locked" | Another run is working on it, or a crashed run | Wait; else 11.15 |
| No alert emails | NOTIFY not scheduled / not triggered, no `email_recipient` in its Variable, no `smtp_default` | Check 12.6 and the NOTIFY Variable |
| Rules never run | Table has an export and the batch is not exported yet | Normal: rules wait for the export |
| FILE_CHECK failed but FILE_LOAD started | At least one file passed (`check_passed`); the failed file is in `Error/` (`CHECK_MOVE_FAILED`) with its error log | Expected: the good files load; fix and resend the failed file. `file_check_result` keeps the run failed |
| FILE_CHECK failed and FILE_LOAD did not start | No file passed (`check_passed` skipped) | Fix and resend the files; the next FILE_CHECK starts FILE_LOAD |
| Teradata pull fails "Object '…_$ENV_V…' does not exist" | `$env` is not replaced in `Src_Sql_Txt` | Name the environment's database in the query (e.g. `CMSUNIV_CORE_DEV_V`) |

---

## 14. Glossary

| Term | Meaning |
|---|---|
| Project | A compliance program, e.g. CMS Universes (`Project_Cd`) |
| Table | A dataset of the project, e.g. ODAG3 (`Table_Nm`); has staging, core and optionally export tables |
| Table group | Tables run together, e.g. ODAG1–5 |
| Source | Who provides the data: an FDR sending files, or an internal system read by a query (`Src_ID`) |
| Run type | How often / why batches are made: DLY, WKLY, MNT, ad hoc (`Run_Ty`) |
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

---

## 15. CMSUNIV setup at a glance

The rows are in `airflow_resources/examples/cmsuniv_setup.sql` (exported by the framework's copy into the existing
feed tables, no procedure, no PROVISIONING_WORK; Variables in `cmsuniv_dev/`).

| What | CMSUNIV |
|---|---|
| Project | `CMSUNIV`; metadata `HSABC_DEV`; staging `CMSUNIV_FILELAND_$ENV_T` (files) / `CMSUNIV_STAGE_$ENV_T` (internal), core `CMSUNIV_CORE_$ENV_T`, export `EXPORT_FEED_$ENV_T.SDO_CMSUNIV_<table>_2022` (batch in `Btch_ID`; the run types exported are the EXPORT_LOAD DAG's scope) |
| Table groups | `ODAG` (ODAG1 – 6), `CDAG` (CDAG1 – 7), `FA` (FA1 – 4), `SNPCC` (SNPCC1) |
| Sources | 55: FDRs send files to `chs_dms/Dev/OSS_Inbound/External/CMSUNIV`; internal: QNXT 10 (`SQL_CHS`), AZ 40 (`SQL_AZ`), SNPCC 11 (`SQL_SNPCC`), Facets 210 (Teradata views `CMSUNIV_CORE_DEV_V.CSF_ODAG_Table3_2022` / `CSF_FA_Table4_2022` on the framework's connection, by `Batch_ID`; staging `CMSUNIV_STAGE_$ENV_T.ODAG_CSF_TABLE3_2022` / `FA_CSF_TABLE4_2022`, IDs by `ROW_SEQUENCE`) |
| Version | `2200` from 1900-01-01 on every crosswalk row |
| Row IDs | Every core ID column has a `ROW_SEQUENCE` row: files and sources whose query returns no ID are numbered by the framework (above the highest in staging and core); an ID the query returns is kept |
| Export feed columns | `'*'` rows map the feed's own names: `ETL_LOAD_DT_TM` / `ETL_LAST_UPDT_DT_TM` → `LOAD_TIMESTAMP`, `STG_CRET_DTM` → `LOAD_DATE`, `UNVRS_VRSN_CD` → `VERSION`, `FILE_NM` → `FILE_NAME`; `SDO_CMSUNIV_<table>_2022_KEY` → `ROW_SEQUENCE` (copied from the core ID); the data columns pair by column order |
| File names | `CMSAUTH_<source>_<file type>_22_<run type>_<report start>_<report end>_<timestamp>.txt`, e.g. `CMSAUTH_CDHDHMO_PYMT_C_22_DLY_20261019_20261019_20261014104600.txt` |

| Run type | Category | When created | Report dates | SLA (close) | Who |
|---|---|---|---|---|---|
| `DLY` | SCHEDULED | Every day (`Sched_Cd DAILY`) | Previous day (`PREV_DAY`); FA tables their own | 1 (same day) | Every source |
| `WKLY` | SCHEDULED | Every Sunday (`WEEKLY SUN`) | The 60 days ending the Saturday before (`RANGE(DAY,60,60)`) | 5 (Friday) | Internal sources |
| `MNT` | SCHEDULED | The Sunday before the 3rd Tuesday (`MONTHLY TUE 3`, offset -2) | Previous calendar month | 5 | Every source |
| `IN1` | ADHOC with a daily schedule | Every day, SNPCC1 / 11 only | Previous day | 1 | Pulled / exported daily; with data stays open until released on BATCH_CLOSE, without data closes as `DATA_NOT_PROVIDED`, one alert a day |
| `CMS` | ADHOC | Only from `ComplianceRequestInTake` | The request's dates | 1 | Pulled / exported like scheduled; closes when released (with data); the only run type with carry-forward |

FA report dates (every run type): FA1 `LAST_DAYS(MONTH,1,14)` (the last 14 days of the previous month), FA2
`ANNUAL_WINDOW(01-01,01-31)`, FA3 `ANNUAL_WINDOW(11-01,12-31,02-01)`, FA4 `ANNUAL_WINDOW(11-01,01-31)`; FA2 – FA4 move
to the new year's dates on 1 February.

DAG chain (Variables in `cmsuniv_dev/`): FILE_CHECK → FILE_LOAD → OVERRIDE_DECISIONS, EXPORT_LOAD; SOURCE_LOAD →
EXPORT_LOAD → FILE_RULES (waits); BATCH_CLOSE → BATCH_CREATION, NOTIFY. No schedules yet. Rules: GRE rule groups
named like the tables (`ODAG1` …, `CDAG1` …), variants = run types or empty, read `EXPORT_FEED_$ENV_V.SDO_CMSUNIV_*`.

---

## 16. Upgrading an existing installation

`airflow_resources/sql/schema.sql` always has the current tables. For tables created with an earlier version, run
the statements of every version after yours, in order (replace `HSABC_DEV`). To find what an installation still
lacks, run `airflow_resources/sql/schema_check.sql`: it lists every column that is missing or whose NULL / NOT NULL
differs from `schema.sql` (no rows = up to date). A NOT NULL column that should allow NULL is fixed with
`ALTER TABLE HSABC_DEV.<table> ADD <column> NULL;`.

| From before | Statements |
|---|---|
| 0.3.57 | `ALTER TABLE HSABC_DEV.ComplianceRunTypePeriod ADD Rpt_Dt_Sql_Txt VARCHAR(8000);` `ALTER TABLE HSABC_DEV.ComplianceRunTypePeriod ADD Rpt_Period_Cd NULL;` |
| 0.3.60 | `ALTER TABLE HSABC_DEV.ComplianceFileLoad RENAME Quarantine_Rsn_Cd TO Reject_Rsn_Cd;` `UPDATE HSABC_DEV.ComplianceFileLoad SET Load_Stat = 'REJECTED' WHERE Load_Stat = 'QUARANTINED';` |
| 0.3.62 | the `Load_Stat` renames below |
| 0.3.63 | the timeline event renames below |
| 0.3.67 | `ALTER TABLE HSABC_DEV.ComplianceExportConfig ADD Exp_Sql_Txt VARCHAR(8000), ADD Exp_Pre_Sql_Txt VARCHAR(8000), ADD Exp_Post_Sql_Txt VARCHAR(8000);` |
| 0.3.77 | `ALTER TABLE HSABC_DEV.ComplianceExportConfig DROP Exp_Btch_Col_Nm;` The extra export batch column (`Exp_Btch_ID`) and the `{exp_btch_id}` placeholder are gone: export tables carry the batch in `Btch_ID` only. Rows whose `Exp_Proc_Parm_Txt` / `Exp_Pre_Sql_Txt` / `Exp_Post_Sql_Txt` use `{exp_btch_id}` change it to `{btch_id}` (`validate-config` lists them); an empty `Exp_Proc_Parm_Txt` now means `{btch_id}, {data_btch_id}` |
| 0.3.71 | `airflow_resources/sql/upgrade_0.3.71.sql`: the audit columns of every framework table renamed (`Created_Dtts` → `Load_Dtts`, `Created_By` → `LoadUser_ID`, `Updated_Dtts` → `LastUpdt_Dtts`, `Updated_By` → `LastUpdt_ID`); six tables (`ComplianceRunType`, `ComplianceDataSetSourceXwalk`, `ComplianceSourceFileConfig`, `ComplianceRequestControl`, `ComplianceFileLoad`, `CMS_ComplianceExceptionsAudit`) backed up, recreated in the new column order (audit columns last) and filled again, then the backups dropped; `ComplianceRequestControl` and `ComplianceFileLoad` get `LoadUser_ID` / `LastUpdt_ID`. Fill the four `{{NEXT_...}}` start values from step 2.0 first so the IDs are kept. Every framework update now sets `LastUpdt_ID` to the database user. Run it with the DAGs paused, then deploy the code |

```sql
-- 0.3.62: load statuses
UPDATE HSABC_DEV.ComplianceFileLoad
SET Reject_Rsn_Cd = CASE WHEN Load_Stat = 'DEFERRED' THEN 'STAGING_TABLE_BUSY' ELSE Reject_Rsn_Cd END,
    Load_Stat = CASE Load_Stat WHEN 'PROMOTED' THEN 'FILE_LOADED' WHEN 'SUPERSEDED' THEN 'RELOADED'
                               WHEN 'FAILED_TECHNICAL' THEN 'PROCESS_FAILURE' WHEN 'DEFERRED' THEN 'LOAD_INCOMPLETE' END
WHERE Load_Stat IN ('PROMOTED', 'SUPERSEDED', 'FAILED_TECHNICAL', 'DEFERRED');

-- 0.3.63: timeline events
UPDATE HSABC_DEV.ComplianceRequestFileDetail
SET Event_Ty = CASE Event_Ty WHEN 'FILE_PROMOTED' THEN 'FILE_LOADED'
                             WHEN 'LATE_ARRIVAL_PROMOTED' THEN 'LATE_ARRIVAL_LOADED'
                             WHEN 'CORRECTION_PROMOTED' THEN 'CORRECTION_LOADED' END
WHERE Event_Ty IN ('FILE_PROMOTED', 'LATE_ARRIVAL_PROMOTED', 'CORRECTION_PROMOTED');
```

Renaming a run type (e.g. `DAILY` → `DLY`) is a configuration change, not an upgrade: insert the new
`ComplianceRunType` row, change the `Run_Ty` of its `ComplianceRunTypePeriod` rows and the run type lists of the
crosswalk, then set the old run type's `Active_Ind = 0`. Batches already created keep their old batch IDs; let them
close first.
