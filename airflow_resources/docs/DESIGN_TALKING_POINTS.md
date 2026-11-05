# Compliance Batch Framework – Design Talking Points

Speaker notes for walking a meeting through the design with the draw.io diagrams
([`diagrams/compliance_framework.drawio`](diagrams/compliance_framework.drawio), one page per section below; the
pictures are in [`diagrams/png/`](diagrams/png/)). Each section says what the page shows, the points to make, why it
was built that way, and the questions to expect. The operational detail (exact columns, codes, procedures) stays in
the [runbook](OPERATIONAL_RUNBOOK.md).

Suggested order and time (about 60 minutes): the problem and the principles (5), architecture (10), daily flow and
the common DAG behaviour (10), the step pages (20), batch lifecycle and metadata tables (10), questions (5).

---

## 0. Opening: the problem and the design principles

**The problem.** Compliance reporting (e.g. the CMS Universes) needs data from many sources – external vendors that
send files and internal systems that are read by query – for many datasets, on daily, weekly, monthly and ad-hoc
cycles. For every cycle we must be able to say: what was expected, what arrived, when, what was loaded and exported,
what was missing, and who approved any exception.

**The answer.** One generic framework that runs every project the same way. A project is added with metadata rows,
not code.

Principles to state up front (each comes back on a later page):

1. **Metadata driven.** Projects, tables, sources, schedules, file layouts, checks, exports and rules live in
   Teradata tables. The Python code holds no project names, tables or file layouts.
2. **The batch is the unit of work.** Everything – loads, exports, rules, alerts, closing – is tied to a batch:
   one table + run type + report period + request day, with one row per source.
3. **Every step is safe to repeat.** A rerun only does what is still to do: a batch is never created twice, a file
   is never loaded twice, an export replaces its own rows. Recovery is "run it again".
4. **Nothing is half done.** Core and export writes are single transactions; a failure leaves the previous state.
5. **Everything is recorded.** A timeline per batch and an exceptions audit per problem or approval; the audit is
   what gets emailed.
6. **Steps are separate DAGs.** Each step can be scheduled, triggered, rerun or held on its own, and a failure in one
   does not hide the others' state.
7. **No secrets in code or metadata.** Credentials come only from Airflow Connections; passwords and file data are
   never logged.

---

## Page 1 – Architecture

**What it shows.** Four zones: data sources, the NAS, Airflow, Teradata; plus people and outputs.

**Walk through it left to right:**

- **Data sources.** External senders (FDR vendors) deliver delimited files to the NAS. Internal systems (SQL Server
  sources such as SNPCC, QNXT, AZ, and Teradata views) are not sent as files: the framework queries them.
- **NAS (SMB share).** One inbound folder per project. Next to it the framework keeps `Archive/` (loaded files, by
  year if wanted), `Error/` (rejected files), `ErrorLog/` (why each one was rejected, record by record) and
  `ControlReport/` (one line per file per run). The business can see what happened to a file without database access.
- **Airflow 3.3.**
  - The **scheduler** starts DAGs on Central-time schedules; the **triggerer** handles deferred waits (EXPORT_LOAD
    waiting for FILE_RULES without holding a worker slot).
  - **Ten DAGs per project, one per step** (CMSUNIV: `OSSTD_CORE_CMS_COMPLIANCE_*`; UMCM: `COMPLIANCE_UMCM_*`).
    The DAG file is only a shell: the step, schedule, scope, triggers and settings come from **one Airflow Variable
    per DAG** (JSON).
  - **Connections** hold Teradata, NAS, SQL Server sources and SMTP. Nothing else holds a credential.
  - The **workers** run the `compliance_framework` Python package in process; the **GRE** (`rules_engine`) runs the
    data quality rules, in a **pool of one slot** so two rules runs never write the GRE tables at once.
- **Teradata.**
  - **Metadata database** (`HSABC_$ENV`): configuration, batches, loads, timeline, audit, locks.
  - Per project **staging** (`<PROJECT>_FILELAND_$ENV_T`) and **core** (`<PROJECT>_CORE_$ENV_T`) databases.
  - The **export feed** (`EXPORT_FEED_$ENV_T`), written by the framework or by **export procedures**
    (`HSPROCS_$ENV`).
  - The **GRE tables** (`gre_results`, `gre_exceptions`), keyed by the batch ID.
  - `$ENV` is filled per environment (DEV / QA, empty in UAT / PROD), so the same metadata works everywhere.
- **People and outputs.** Operations / BA fill the configuration, overrides and ad-hoc requests. Alerts go out by
  email (NOTIFY and the DAG success / failure emails). A failed DAG run opens a **ServiceNow incident**. Downstream
  consumers read the export feed.

**Why this shape:**

- Staging and core are separate: staging takes the file as it is (text), core has the real types. Type problems are
  found **before** core is touched, with the column named.
- The export table is separate from core: consumers get a stable feed, filled only when a batch is due or released,
  and replaced as a whole per batch.
- Airflow orchestrates; Teradata holds all state. A worker can die at any point and the next run continues from the
  tables.

**Questions to expect:**

- *Where are credentials?* Only in Airflow Connections; never in Variables, metadata, tfvars or commits; never logged.
- *Why not one big DAG?* Steps run on different clocks (files hourly, internal pulls after their source loads,
  export in the evening, close at night) and must be rerun or held one at a time.
- *What if the NAS or Teradata is down?* The step exits with code 2, Airflow retries, then raises an incident.
  Nothing is half written.

---

## Page 2 – Daily flow (DAGs and triggers)

**What it shows.** The ten DAGs, their default times, and which DAG starts which.

**The day in order:**

1. **BATCH_CREATION** (06:00, and after BATCH_CLOSE) creates the batches that are due – so there is always a batch
   waiting before a file or pull arrives.
2. **FILE_CHECK** (optional, e.g. every 15 minutes) checks waiting files and loads nothing; when a file passes it
   starts FILE_LOAD.
3. **FILE_LOAD** (hourly, or after FILE_CHECK) loads files NAS → staging → core; starts OVERRIDE_DECISIONS and
   EXPORT_LOAD.
4. **SOURCE_LOAD** (e.g. 06:30, after the source systems' own loads) pulls internal sources; starts EXPORT_LOAD.
5. **OVERRIDE_DECISIONS** applies approved carry-forwards and reopens.
6. **EXPORT_LOAD** (after the loads, and at 20:00) writes the export feed, then starts **FILE_RULES** and waits.
7. **BATCH_CLOSE** (22:00) closes what is past its close date; starts BATCH_CREATION (next day) and NOTIFY.
8. **NOTIFY** (after BATCH_CLOSE, and hourly) emails the open alerts.

**Points to make:**

- Solid arrows are `trigger_dags` in each Variable, fired **only on success**. A failed load does not push bad data
  downstream.
- Dashed arrows are ordering rules, not triggers: batches exist before data; carry-forwards before the close; the
  evening export before the close.
- FILE_LOAD and SOURCE_LOAD are independent. A table loaded by both waits its turn (staging-table lock).
- Keep FILE_LOAD out of the window between BATCH_CLOSE and BATCH_CREATION; a file arriving then finds its batch
  closed and is rejected (it can be accepted later with an override).

**Why:** every step also has its own schedule, so the chain still runs when a trigger is missed, and any step can be
started by hand with a narrower scope.

---

## Page 3 – What every DAG run does

**What it shows.** The common skeleton of all ten DAGs and the meaning of the result.

- A run reads its Variable and the trigger's settings (scope, runs, settings), then runs the step – one task per
  `runs` entry (for example one task per table group).
- Every step ends with a JSON **step report** in the task log and an **exit code**:
  - **0 – done**: the next DAGs are triggered, `success_email` is sent.
  - **1 – done, with problems** (a rejected file, a failed export): the task fails at once, **no retry**, ServiceNow
    incident and `fail_email`; the next DAGs do not start.
  - **2 – could not run** (database down, missing table, bad Variable): retried after 5 minutes, then incident and
    email.
- `wait_for_triggered` (EXPORT_LOAD → FILE_RULES): the run waits in deferred mode until the started run ends; its
  failure fails this run.
- `fail_on_problems = false` turns exit 1 into success when a problem is only informational (OVERRIDE_DECISIONS).

**Why exit 1 and 2 differ:** retrying a rejected file gives the same answer – it needs a person. Retrying an outage
often works. A **skip** (a file still arriving, another project's file) is neither: it is logged with its reason and
never fails the job.

**Other guarantees to mention:** a DAG never runs twice at once (it waits for its previous run); different DAGs can
overlap safely because batch and table locks keep them apart.

---

## Page 4 – BATCH_CREATION

**What it shows.** Where batches come from and the checks before one is created.

- **Sources of batches:** schedule codes on the run type (daily, weekly on a weekday, monthly on a day, with offsets
  and a catch-up window), a SQL statement that decides the dates, the batch calendar for irregular dates, an ad-hoc
  run type with a schedule, and **ad-hoc requests** in `ComplianceRequestInTake`.
- **Which sources:** every crosswalk row (`ComplianceDataSetSourceXwalk`) active and effective on the day that lists
  the run type.
- **Exists?** One batch per table + run type + report period + day. Running it twice creates nothing new.
- **Result:** one `ComplianceRequestControl` row **per source**, status `REQUEST_CREATED`, timeline
  `BATCH_CREATED`.
  - Batch ID `YYYYMMDD_PROJECT_TABLE_RUNTYPE` (e.g. `20261020_CMSUNIV_ODAG3_DLY`), readable in every table and log.
  - The compliance version comes from the crosswalk row effective that day, so a yearly protocol change is a dated
    metadata change, not a code change.
  - Close date = `Req_Dt_Key + SLA_Days − 1`.
- A configuration error in one row is reported (exit 1); the other batches are still created.

**Why:** "what was expected" must exist before anything arrives. Without a batch there is nothing to compare a file
against, and no way to say a source was missing.

---

## Page 5 – FILE_CHECK (optional)

**What it shows.** A pre-check of waiting files that loads nothing.

- Leaves files modified in the last `NAS_MIN_AGE_SECONDS` (120 s) alone – they may still be arriving.
- A file that belongs to another project or run type is skipped, with the reason in the log.
- The name must fit exactly one file name template and have an open batch for its dates.
- Content checks from `ComplianceFileCheckConfig`: header, trailer (with row counts), column count, record types,
  allowed values.
- **Passed:** `FILE_CHECK_PASSED`, and FILE_LOAD is started for the good files right away.
- **Failed:** `FILE_CHECK_FAILED` (emailed), an error log in `ErrorLog/`, and with `CHECK_MOVE_FAILED` the file moves
  to `Error/` so FILE_LOAD does not pick it up again. The run is marked failed (`file_check_result`) so it is
  visible, but the good files still load.

**Why optional:** some sources want fast feedback ("your file is wrong") many times a day before the hourly load.
FILE_LOAD can run the same checks itself (`CHECK_ON_LOAD`) when no separate pre-check is wanted.

---

## Page 6 – FILE_LOAD (NAS to core)

**What it shows.** One file's way from the inbound folder to core, and every exit.

1. **List** the inbound folders of the scope.
2. **Skip, not reject**, when the file is still arriving, is another project's or run type's, or was already moved
   or loaded by another run. The reason is in the log and in the report's `skipped_files`.
3. **Register** the load: a `ComplianceFileLoad` row (`RECEIVED`); the name gives the file config and the batch.
4. **Batch open?** If not: rejected (`FILE_REJECTED_NO_BATCH` / `_BATCH_CLOSED`), unless an approved
   `LATE_ARRIVAL` / `CORRECTION` override lets it in.
5. **Content checks** (when `CHECK_ON_LOAD`).
6. **Staging free?** If another load holds the staging table: `LOAD_INCOMPLETE`, picked up by the next run.
7. **Stage:** remove the source's earlier rows of the run type, insert the records plus the **derived columns**
   (batch ID, source, version, load time, row sequence …). The engine is batched inserts or Teradata **FastLoad**
   (`LOAD_ENGINE` `AUTO` picks FastLoad from 100 MB; if FastLoad is not allowed it falls back to inserts in the same
   run, with a warning).
8. **Type checks before core:** every NOT NULL core column must get a value, and every value must convert to its
   core column's type and `FORMAT`. If not: `CORE_TYPE_MISMATCH`, column named, staged rows removed, **core
   untouched**.
9. **Core, one transaction:** remove the batch's core rows of this source, copy the staged rows in (converted); the
   copied count must equal the staged count.
10. `FILE_LOADED`, batch `SOURCE_DATA_READY` (or `…_NO_DATA` for an empty file); file to `Archive/`; control report
    line; triggers OVERRIDE_DECISIONS and EXPORT_LOAD.

Every rejection moves the file to `Error/` with an error log, writes an audit event (emailed) and ends the run with
exit 1 – after all the other files are done. A database error at any step is `PROCESS_FAILURE`: nothing half done,
the file stays and is tried again.

**Why:**

- **Delete-then-insert per batch and source** makes a resend replace the earlier data instead of doubling it; the
  previous load becomes `RELOADED`.
- **Staging as text, checked before core** turns "the load crashed" into "column X, value Y on line N does not fit".
- **The file is identified by path, size and time**, so the same name delivered again is a new delivery, and the
  same content for another batch is loaded and reported.
- **Real numbers:** a 1.2 GB file (4.6 million rows) staged in about 26 minutes with batched inserts; download and
  core copy take seconds. FastLoad is the lever for large files.

---

## Page 7 – SOURCE_LOAD (internal sources by query)

**What it shows.** The same staging → core path, but the data is pulled with a query instead of read from a file.

- Works on open batches whose source has a `ComplianceInternalSourceConfig` row: the query, the Connection (SQL
  Server, or Teradata itself), and the target tables.
- **Held run types** are skipped and listed in the report.
- The query runs with the batch's values bound as parameters; the log shows it with the values written in, so it can
  be copied and run by hand.
- **No rows yet:** before the close date the source is `WAITING` and pulled again on each run; on or after the close
  date "no rows" is accepted as `SOURCE_DATA_READY_NO_DATA`.
- Query failure → `SOURCE_QUERY_FAILED` with the driver's message; columns that do not fit → rejected with the
  names.
- Then the same type checks and core transaction as FILE_LOAD.

**Why one path:** files and queries end in the same staging, core, timeline and audit, so exports, rules and the
close do not care how the data arrived.

---

## Page 8 – OVERRIDE_DECISIONS (approved exceptions)

**What it shows.** How human decisions are applied – always through an approved row in `ComplianceBatchOverride`.

- **REUSE (carry forward):** a source will not send this period; the batch takes the data of the earlier closed batch
  (`SOURCE_DATA_CARRY_FORWARD`). Only before the close, and only for run types that allow it (`Carry_Fwd_Ind = 1`).
- **REOPEN:** a closed batch is opened again until new data arrives, then BATCH_CLOSE closes it again.
- **Expired or rejected REUSE:** the batch goes back to `REQUEST_CREATED`.
- **LATE_ARRIVAL / CORRECTION** are not applied here: FILE_LOAD uses them when the file comes in.
- An override that cannot apply is reported (`OVERRIDE_INVALID_DETECTED`, emailed); the task still succeeds.

**Why:** exceptions are data, with an approver and a validity date, so the audit shows who decided what and when.
Nobody updates a status by hand.

---

## Page 9 – EXPORT_LOAD (core to the export feed)

**What it shows.** Which batches are exported and the three ways to export.

- **Which batches:** every **open** batch of the scope, on every run; a **closed** batch again only when it got new
  data since its last export (late arrival, correction); or the batches named in the trigger.
- **Due:** from the close date (and `RELEASE_TIME` that day, if set), or earlier when released; held run types wait.
- **Which data:** the newest batch of the period (`NEW_FILE`, `CARRY_FORWARD` or `NO_DATA`); older batches of the
  same period are skipped.
- **How**, per source:
  - an **export procedure** (`Exp_Proc_Nm`) called with the batch's values (CMSUNIV uses
    `sp_OSS_DA_PROV_TO_EXPORT_LOAD_CMSUNIV`);
  - a **custom SELECT** (`Exp_Sql_Txt`);
  - or a **plain copy** with mapped columns.
- Optional pre / post SQL; **pre, every source's export and post run as one transaction per batch**. A failure rolls
  back and is reported (`EXPORT_LOAD_FAILED`).
- `EXPORT_LOADED` in the timeline, then FILE_RULES is started and waited for.

**Why:**

- Exporting every open batch on every run means corrections and late data reach the feed without anyone asking.
- A re-export replaces the batch's own rows (batch ID + source), so it can run any number of times.
- Waiting for FILE_RULES keeps the export tables still while the rules read them.

---

## Page 10 – FILE_RULES (data quality)

**What it shows.** The GRE rules run per exported batch.

- Takes the loads whose rules have not run yet (or ended in error), batch by batch; a table with an export only
  after its export.
- Rule groups come from `ComplianceRuleBinding`, otherwise from the table and run type.
- `run_key = Btch_ID`, and the rules see only the batch's rows. Results and exceptions join back to the batch, and a
  rerun refreshes them.
- **Results:** `PASSED`; `FAILED` (emailed – a result, not an error, so the task succeeds); no active rules →
  `SKIPPED`; engine error → `ERROR`, tried again next run, task fails.
- Runs in the one-slot pool `gre_rules`.

**Why per batch:** a rule failure points at one batch of one table, which is what the business has to fix or
explain.

---

## Page 11 – BATCH_CLOSE

**What it shows.** How a batch ends.

- Takes open batches whose close date has come. Held run types and reopened batches still waiting for data stay open;
  ad-hoc batches with data stay open until released.
- A batch being worked on right now is closed by the next run (`BATCH_CLOSE_DEFERRED_LOCKED`).
- **Closed as:**
  - data loaded or carried forward → `REQUEST_COMPLETE`;
  - the source said "no data" → `DATA_NOT_PROVIDED` (`NO_DATA`);
  - **nothing arrived** → `DATA_NOT_PROVIDED` (`MISSING`) plus `SOURCE_MISSING_AT_CLOSE`, emailed.
- Then starts the next day's BATCH_CREATION and NOTIFY.

**Why:** the close is what turns "not yet" into "missing" – the compliance answer to "did every source deliver for
this period?".

---

## Page 12 – NOTIFY

**What it shows.** How business alerts are emailed.

- Reads the audit events not yet sent (`Notified_Ind = 0`), sends one email per event, marks it sent; a failed send
  stays for the next run. Two runs never send the same event (event lock).

**Why separate from the DAG emails:** many business problems (a missing source, a rejected file in a run that still
loaded others, a stuck lock) do not fail any DAG. The DAG emails say "the job ran or failed"; NOTIFY says "this batch
needs attention".

---

## Page 13 – The life of a batch

**What it shows.** The statuses of one batch row (one per source).

- `REQUEST_CREATED` → data arrives → `SOURCE_DATA_READY`; an empty file, or no rows on the close date →
  `SOURCE_DATA_READY_NO_DATA`; approved REUSE → `SOURCE_DATA_CARRY_FORWARD`.
- At the close: `REQUEST_COMPLETE` (`NEW_FILE` / `CARRY_FORWARD`) or `DATA_NOT_PROVIDED` (`NO_DATA` / `MISSING`).
  These are final.
- After the close: LATE_ARRIVAL / CORRECTION load data and the batch stays closed; REOPEN opens it until new data.

**Point to make:** status and resolution together answer the compliance question per source and period, and the
timeline (`ComplianceRequestFileDetail`) shows every step that led there, with times.

---

## Page 14 – Metadata tables (how and why)

**What it shows.** Who fills which table and how they connect. Blue = the business fills it; purple = the framework
writes it (read only).

**Configuration – the "what should happen":**

| Table | Role | Why it exists |
|---|---|---|
| `ComplianceProject` | The project (`Project_Cd`) | Everything is scoped by project; several projects share the framework |
| `ComplianceTable` | Table groups (optional) | Run or schedule several tables together |
| `ComplianceSourceSystem` | Sources (`Src_ID`, name, type) | One definition per source, reused across tables |
| `ComplianceDataSetSourceXwalk` | Who sends which table, for which run types, from when, in which version | The heart of the setup: effective dates make version changes and stopping a source dated changes |
| `ComplianceRunType` | Schedule codes, SLA days, category (scheduled / ad hoc) | When batches are created and when they close |
| `ComplianceRunTypePeriod` | Report dates per run type (and table) | The same run type can cover different periods per project |
| `ComplianceBatchCalendar` | Extra dates (optional) | Irregular cycles without code |
| `ComplianceSourceFileConfig` | File name template, folder, delimiter, header / trailer, staging and core tables | Files are matched and loaded from metadata |
| `ComplianceInternalSourceConfig` | Query, Connection, staging and core tables | Internal sources without files |
| `ComplianceFileCheckConfig` | File checks | Reject bad files with a precise reason |
| `ComplianceTargetColumnConfig` | Derived columns (batch ID, version, load time, row ID …) | Lineage on every row, without changing the source file |
| `ComplianceExportConfig` | Export table, procedure or SELECT | Each table decides how it feeds downstream |
| `ComplianceRuleBinding` | GRE rule groups (optional) | Rules other than the default per table / run type |

**Requests and decisions – the "what people asked for":**

| Table | Role |
|---|---|
| `ComplianceRequestInTake` | Ad-hoc batch requests |
| `ComplianceBatchOverride` | REUSE, LATE_ARRIVAL, CORRECTION, REOPEN – with an approver and validity dates |

**Written by the framework – the "what happened":**

| Table | Role | Key |
|---|---|---|
| `ComplianceRequestControl` | The batches: one row per batch + source, status and resolution | `Req_ID`, `Btch_ID` |
| `ComplianceFileLoad` | One row per load or pull: file, counts, status, reject reason | `Load_ID` → `Req_ID` |
| `ComplianceRequestFileDetail` | The timeline of each batch row | `Req_ID` |
| `CMS_ComplianceExceptionsAudit` | Problems and approvals; what NOTIFY emails | event, batch |
| `ComplianceLock` | Work in progress (batch, staging table, event); expires after a crashed run | lock key |

**How they connect, in one sentence each:**

- Project → crosswalk (table + source + run types + version) → **BATCH_CREATION** writes `ComplianceRequestControl`.
- Each load or pull writes a `ComplianceFileLoad` row for that batch row, and every event a timeline row.
- Intake requests create batches; overrides change what happens to existing ones.
- Every problem or approval becomes an audit row, which NOTIFY emails.

**Why this split:** configuration, decisions and results are separate, so the business can change setup and approve
exceptions with plain SQL inserts, while the result tables stay trustworthy because only the framework writes them.
`validate-config` checks the configuration before it is used.

---

## Closing points

- **New project = metadata + Variables + copied DAG shells.** UMCM was set up this way next to CMSUNIV.
- **Operations:** every SQL the framework runs is logged with its parameters and a runnable copy; every skip,
  deferral and failure is logged with its reason; the step report summarises each run.
- **Recovery is rerunning.** Steps are safe to repeat, transactions are all-or-nothing, locks expire after a crash.
- **Audit:** for any batch and source, the tables answer what was expected, what arrived, when, what was rejected
  and why, what was exported, what the rules found, and who approved any exception.

## Questions you may get

| Question | Answer |
|---|---|
| What happens if a vendor resends a file? | It replaces the batch's data for that source (delete then insert); the earlier load is marked `RELOADED`. After the close it needs a CORRECTION override |
| What if a file comes after the close? | Rejected as `FILE_REJECTED_BATCH_CLOSED` until a LATE_ARRIVAL / CORRECTION (or REOPEN) override is approved; then it loads and the batch stays closed |
| Can two DAGs collide on the same table? | No: batch and staging-table locks; a busy table is `LOAD_INCOMPLETE` and picked up by the next run |
| What if a worker dies mid-load? | The transaction rolls back; the lock expires; the next run loads the file again |
| How do we know a source never delivered? | BATCH_CLOSE marks it `DATA_NOT_PROVIDED` / `MISSING` and emails `SOURCE_MISSING_AT_CLOSE` |
| How is a yearly protocol change handled? | New crosswalk rows with the new version from the cutover date and new tables; a template script does it; no code change |
| Why Teradata FastLoad only sometimes? | It needs CREATE / DROP TABLE rights and a utility slot; `AUTO` uses it for large files and falls back to inserts when it is not allowed |
| Where do the passwords live? | Airflow Connections only; never in Variables, metadata or logs |
| How do we add a source? | A source row, a crosswalk row, a file (or query) config and checks; run `validate-config` |
