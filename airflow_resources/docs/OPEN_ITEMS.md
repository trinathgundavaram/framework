# Compliance Batch Framework (Airflow / Teradata): open items

State at version 0.3.69. Replaces the earlier `REVIEW_RECOMMENDATIONS.md` (0.3.27) and `BUSINESS_REVIEW.md` (0.3.41);
everything they list that is not below is done. Tested: 25 scenarios on Python 3.13 and 3.9 against a PostgreSQL
stand-in for Teradata; the 9 DAG files parse and a step DAG runs (stubbed step) on Airflow 3.3.0. **Nothing has run on Teradata, the NAS or a real
Airflow deployment yet.**

## 1. Before the first production run

### DEV rehearsal (one full cycle on the real systems)

Create the tables (`sql/schema.sql`), the CMSUNIV staging / core tables (the export goes to the
existing `EXPORT_FEED_DEV_T.SDO_CMSUNIV_*` tables), load `examples/cmsuniv_setup.sql`, the Variables (`examples/cmsuniv_dev`) and
the Connections (`DEV`, `comp_oper_creds`, `SQL_CHS`, `SQL_AZ`, `SQL_SNPCC`, `smtp_default`). Then:

| # | Case | Expected |
|---|---|---|
| 1 | `validate-config` | No ERROR; `HEADER_NAMES_PARTIAL` warnings for the tables without a target list (CDAG1 – 5, FA1 – 4) |
| 2 | BATCH_CREATION on a Sunday before the 3rd Tuesday | DLY, WKLY, MNT, IN1 batches; FA tables with their fixed dates |
| 3 | One good and one bad file per file layout (general, MedHOK) | Good: `FILE_LOADED`, file in `Archive/`. Bad: `REJECTED`, file in `Error/`, error log in `ErrorLog/`, FILE_LOAD fails at once (no retry), incident + email |
| 4 | SOURCE_LOAD for QNXT, AZ, SNPCC before and after the source tables are loaded | Empty first pull = no data, pulled again on the next runs until the batch closes |
| 5 | An internal query with one misnamed column | Pull rejected `SOURCE_DATA_REJECTED` with the name |
| 6 | EXPORT_LOAD (procedures) after the due date; ad hoc (CMS) only with `release` | Export rows stamped with the batch ID; re-export replaces them |
| 7 | BATCH_CLOSE the next day | With data `REQUEST_COMPLETE`; without `DATA_NOT_PROVIDED` + `SOURCE_MISSING_AT_CLOSE` email |
| 8 | `LATE_ARRIVAL` on a closed batch whose source sent an empty file; `REUSE` on an open one | Late file loaded, batch `REQUEST_COMPLETE`; carry-forward applied, empty load superseded |
| 9 | A file whose move to `Archive/` fails (remove delete rights on the folder) and the next run | Loaded once, `FILE_MOVE_FAILED` emailed once, the next runs only retry the move |
| 10 | FILE_LOAD while SOURCE_LOAD holds the same staging table; a lock left by a killed task | Deferred (no error); after 30 minutes `STAGING_LOCK_STALE` emailed with `release-lock` |
| 11 | A Teradata logon failure (wrong password in the Connection) | Task retried once after 5 minutes, then fails with the incident |
| 12 | NOTIFY | One email per open audit event through `smtp_default` |
| 14 | EXPORT_LOAD of ODAG + CDAG, then FILE_RULES (pool `gre_rules`, triggerer running) | EXPORT_LOAD waits; one GRE run per exported batch, `run_key` = batch ID, only that batch's rows (`Btch_ID` filter); results `PASSED` / `FAILED` on the loads; rule SQL in the log |
| 13 | A file into a core table with typed columns (`DATE`, `TIMESTAMP(0)`, `DECIMAL`); then one with a bad date | Values converted with the core column's `FORMAT`; the bad file `REJECTED` `CORE_TYPE_MISMATCH` naming the column, core unchanged |

### Still to configure

- Facets (210): the upstream process writing the framework batch ID into `Batch_ID` of `CMSUNIV_CORE_DEV_V.CSF_ODAG_Table3_2022` / `CSF_FA_Table4_2022`, and the staging tables `CMSUNIV_STAGE_$ENV_T.ODAG_CSF_TABLE3_2022` / `FA_CSF_TABLE4_2022` (you).
- PROD: the PROD Variables (`td_conn_var`, `meta_db`, `load_env`, emails) and the NAS Connection's server.
- Schedules (`schedule_interval`) when the DAGs go unattended; see section 3.

### Measure in DEV, fix before production if slow

| Item | What | Why it can grow |
|---|---|---|
| C1 | `ROW_SEQUENCE` reads `MAX()` of the core table on every load | Mapped on every CMSUNIV file table; one scan per file |
| C3 | EXPORT_LOAD reads the whole batch timeline (`ComplianceRequestFileDetail`) every run | The timeline is never purged |
| C5 | Staging insert batch size (2,000 rows / 500 KB), single transaction per file | Large files; `LOAD_ENGINE=FASTLOAD` is untested on Teradata |
| – | `execution_timeout_minutes` per step | FILE_LOAD of several large files |

## 2. Decisions still open (business)

| Item | What happens today | Options |
|---|---|---|
| Glue / PostgreSQL copy (`module/`) | Frozen since 0.3.34; different batch IDs and statuses | Retire it (mark frozen) or bring to parity (large) |
| Submission snapshot | Core and export rows are replaced in place; no record of what was submitted when | Keep a copy per export, or let the downstream feed do it |
| Member data in error logs | `ErrorLog/` files on the NAS hold the rejected records | Mask fields, or restrict the folder |
| Approvals | Overrides, intake requests and releases are approved by convention (SQL), not enforced | A reviewer different from the creator, a role check, or an approval screen |
| Who acted | Holds, releases and re-pulls are recorded as `SYSTEM` | Pass the triggering user from Airflow |
| Data quality gate | Rules run per batch after its export (FILE_RULES); a failed rule is reported and emailed, the export stands | Whether a failed rule should hold the submission |
| Retention | Control, audit, timeline, staging, core, export and NAS archive are never purged | A retention period per table and folder |
| Business days | SLA and due dates are calendar days; no holidays | A business calendar if needed |
| Status view | Business users read status through SQL (runbook section 12) | A dashboard over `ComplianceRequestControl` |
| Global run types and sources | Shared by all projects (each project has its own DAGs and Variables) | Per-project run types if projects need different meanings |
| Metadata versioning | A change applies at once, also to batches in flight | Effective dates (the crosswalk has them) or change control |

## 3. Behaviour to know (by design)

- **Retries**: a task that could not run (technical error) is retried once after 5 minutes; a step that reported
  problems (a rejected file, a batch not loaded) fails at once with the incident and email.
- **Triggers** may change the step's inputs and normal settings only; the database, connections, file store, rules
  engine and `period_file` always come from the Variable.
- **Intake requests** need no crosswalk row: one row per source, with its `Src_ID` and `Cmplnc_Vrsn` (both
  required); the source needs a file or internal source config for the table. They make one batch per request day; a multi-day window gives a batch each day, and a day without a
  file closes `DATA_NOT_PROVIDED` with an email. One batch per request = one request day.
- **IN1** batches close without data every day (one email a day). **CMS** (ad hoc) is pulled and exported only with
  `release`; only CMS allows carry-forward.
- **FA DLY** batches are made every day with the FA tables' fixed dates; FA2 – FA4 move to the new dates on 1 Feb.
- **MNT** is made on the Sunday before the 3rd Tuesday only; a missed Sunday needs a manual run with `as_of`.
- **Tables without a target list** (CDAG1 – 5, FA1 – 4; FA2 because its header names `RejectReasonCode6` twice) load
  by position: their staging data columns must be in the file's field order.
- **Internal source queries** must name every column after its staging column (or use `TARGET_COLUMNS`); a returned
  column wins over its derived-column row (not the batch, source or load column).
- **Staging → core**: same names pair by name; data columns with different names pair in table order (keep the
  order the same in both tables).
- **`StagingCreateDate`** is the load date, copied unchanged to core and the export. `Allow_Zero_Rcd_Ind` is kept in
  the table but not used.
- **Admin commands** (`validate-config`, `health`, `locks`, `release-lock`, `close-batch`, `test-connection`) run
  from the command line on a worker (deployment guide, "Admin commands").
- **When scheduling**: let BATCH_CLOSE start BATCH_CREATION (`trigger_dags`) and keep FILE_LOAD out of that window, or
  files for a closed period are rejected; a `REUSE` must be applied before the batch's close run; run SOURCE_LOAD
  after the sources' own loads.

## 4. Technical items deferred (after the rehearsal)

Retry limit for a file that keeps failing technically (it is downloaded again every run); NAS "still arriving"
check uses the worker's clock; lock refresh for runs longer than `LOCK_TTL_MINUTES` (raised automatically to the
task limit + 30); cache of table columns and checks per run; per-day loop of the schedule SQL fallback; timed
`runs` entries matched by index; stale-lock alert has no project (a project-scoped NOTIFY does not send it); a
health list of files whose move keeps failing; the staging lock is waited for while the batch lock is held; CLI
`--release` / `--hold`.
