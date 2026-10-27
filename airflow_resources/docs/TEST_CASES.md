# Compliance Batch Framework – Test Cases (Airflow / Teradata)

**Version:** framework 0.3.69. **Environment:** CMSUNIV DEV (`HSABC_DEV`, `CMSUNIV_FILELAND_DEV_T`,
`CMSUNIV_STAGE_DEV_T`, `CMSUNIV_CORE_DEV_T`, `EXPORT_FEED_DEV_T`, NAS `chs_dms/Dev/OSS_Inbound/External/CMSUNIV`).
Run types `DLY`, `WKLY`, `MNT`, `IN1`, `CMS`. Replace dates, batch IDs and sources with the ones of your run.

**How to use**

- Each case has an ID, preconditions, steps and the expected result. Record **Pass / Fail / Blocked**, the date, the
  DAG run id and the batch IDs used in the result columns of your copy.
- "Trigger X with `{...}`" = Airflow → the DAG → Trigger DAG w/ config → paste the JSON.
- `Q1` … `Q12` are the verification queries in section 0.3; run them with the batch / file of the case.
- "Report" = the step report (JSON) at the end of the task log.
- After any configuration change: run `validate-config` (case CFG-01) first.
- Do not run the cases in PROD. Use test files with dummy member data only.

---

## Contents

0. Prerequisites, test data, verification queries
1. Setup and configuration (CFG)
2. Batch creation (BC)
3. Ad-hoc requests (IN)
4. File check (FC)
5. File load – staging and core (FL)
6. Internal sources – SOURCE_LOAD (SL)
7. Overrides (OV)
8. Export load and extract (EX)
9. Data quality rules – FILE_RULES (RU)
10. Batch close (CL)
11. Notifications (NT)
12. Airflow behaviour: DAGs, triggers, retries, locks (AF)
13. Admin commands (AD)
14. Logging and security (LG)
15. End-to-end scenarios (E2E)
16. Upgrade statements (UP)

---

## 0. Prerequisites, test data, verification queries

### 0.1 Prerequisites (check before testing)

| # | Item | How to check |
|---|---|---|
| P1 | Framework 0.3.67 and `rules_engine` deployed in the DAGs folder; 9 DAGs visible, no import errors | Airflow UI → DAGs; Browse → DAG import errors empty |
| P2 | Metadata tables from `sql/schema.sql` exist in `HSABC_DEV` (20 tables) | Q12 |
| P3 | CMSUNIV configuration loaded (`examples/cmsuniv_setup.sql`), run types `DLY` / `WKLY` | `SELECT Run_Ty, Sched_Cd, SLA_Days FROM HSABC_DEV.ComplianceRunType;` |
| P4 | Staging and core tables created; feed tables `EXPORT_FEED_DEV_T.SDO_CMSUNIV_*` exist with `Btch_ID` and `Src_Data_Key` | `SHOW TABLE` on a few |
| P5 | Airflow Connections: `DEV` (Teradata), `comp_oper_creds` (NAS), `SQL_CHS`, `SQL_AZ`, `SQL_SNPCC`, `smtp_default` | Admin → Connections |
| P6 | 9 Airflow Variables from `examples/cmsuniv_dev/` | Admin → Variables |
| P7 | Pool `gre_rules` with 1 slot; Airflow triggerer running | Admin → Pools; `airflow jobs check --job-type TriggererJob` |
| P8 | GRE tables (`gre_rules`, `gre_results`, `gre_exceptions`, `gre_rule_audit`, `gre_rule_errors`) and CMSUNIV rules | `SELECT RULE_GROUP, RULE_VARIANT, COUNT(*) FROM HSABC_DEV.gre_rules WHERE ACT_IND = 1 GROUP BY 1, 2;` |
| P9 | Export feed views `EXPORT_FEED_DEV_V.SDO_CMSUNIV_*` with a `Btch_ID` column | `SHOW VIEW` |
| P10 | `email_recipient` mailbox reachable | Send a test email from Airflow |

### 0.2 Test files

File name: `CMSAUTH_<source>_<file type>_22_<run type>_<report start>_<report end>_<YYYYMMDDHHMISS>.txt`, e.g.
`CMSAUTH_CDHDHMO_PYMT_C_22_DLY_20261025_20261025_20261026080000.txt`. Prepare, per table under test:

| File | Content |
|---|---|
| GOOD | Header, 3–5 data rows with valid values, trailer with the right count |
| EMPTY | Header and trailer only (0 data rows) |
| BAD_HEADER | One header name changed |
| BAD_COUNT | Trailer count wrong |
| BAD_RECORD | One row with a missing field, one with an extra field, one blank line |
| BAD_VALUE | A value breaking a check (`VALUE_IN`, `DATE_FORMAT`, `LENGTH`) |
| BAD_TYPE | A value that does not convert to its core column's type (e.g. `13/45/2026` into a DATE column) |
| BAD_NAME | Name not matching the template (e.g. version `21`, unknown run type, bad date) |
| NO_BATCH | Report dates for which no batch exists |
| DUP | A copy of GOOD under another timestamp |

### 0.3 Verification queries

```sql
-- Q1 batches of a day (all sources)
SELECT Btch_ID, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key, Req_Stat, Resolution_Ty, Batch_Close_Ind, Cmplnc_Vrsn
FROM HSABC_DEV.ComplianceRequestControl WHERE Project_Cd = 'CMSUNIV' AND Req_Dt_Key = DATE '2026-10-26' ORDER BY Btch_ID, Src_ID;

-- Q2 one batch
SELECT * FROM HSABC_DEV.ComplianceRequestControl WHERE Btch_ID = '<batch>' ORDER BY Src_ID;

-- Q3 timeline of a batch
SELECT c.Src_ID, d.Event_Dtts, d.Event_Ty, d.Load_ID, d.Event_Txt
FROM HSABC_DEV.ComplianceRequestFileDetail d JOIN HSABC_DEV.ComplianceRequestControl c ON c.Req_ID = d.Req_ID
WHERE c.Btch_ID = '<batch>' ORDER BY c.Src_ID, d.Detail_ID;

-- Q4 loads (files and pulls) of a batch, newest first
SELECT Load_ID, File_Share, File_Path, Load_Stat, Reject_Rsn_Cd, Rules_Stat, Stg_Rcd_Cnt, Error_Txt, LastUpdt_Dtts
FROM HSABC_DEV.ComplianceFileLoad WHERE Btch_ID = '<batch>' ORDER BY Load_ID DESC;

-- Q5 alerts (audit) of a batch
SELECT Event_ID, Event_Ty, Sevrty, Src_ID, Load_ID, Event_Txt, Notified_Ind, Event_Dtts
FROM HSABC_DEV.CMS_ComplianceExceptionsAudit WHERE Btch_ID = '<batch>' ORDER BY Event_ID;

-- Q6 staging rows by batch / source / run type / load
SELECT Btch_ID, Src_Data_Key, Run_Ty, AuditDataID, COUNT(*) FROM <staging db>.<table> GROUP BY 1, 2, 3, 4 ORDER BY 1, 2;

-- Q7 core rows of a batch
SELECT Src_Data_Key, COUNT(*), MIN(StagingCreateDate), MAX(Load_Dtts) FROM <core db>.<table> WHERE Btch_ID = '<batch>' GROUP BY 1;

-- Q8 export rows of a batch
SELECT Src_Data_Key, COUNT(*) FROM EXPORT_FEED_DEV_T.SDO_CMSUNIV_<table>_2022 WHERE Btch_ID = '<batch>' GROUP BY 1;

-- Q9 rules results of a batch
SELECT r.rule_id, r.status, r.run_key FROM HSABC_DEV.gre_results r WHERE r.run_key = '<batch>' ORDER BY 1;
SELECT rule_id, run_type, batch_schedule, COUNT(*) FROM HSABC_DEV.gre_exceptions WHERE run_key = '<batch>' AND etl_is_curr_ind = 'Y' GROUP BY 1, 2, 3;

-- Q10 locks
SELECT Lock_Key, Owner_ID, Acquired_Dtts, Expires_Dtts FROM HSABC_DEV.ComplianceLock ORDER BY Acquired_Dtts;

-- Q11 unsent alerts
SELECT Event_ID, Event_Ty, Btch_ID, Event_Dtts FROM HSABC_DEV.CMS_ComplianceExceptionsAudit WHERE Notified_Ind = 0 ORDER BY Event_ID;

-- Q12 framework tables present
SELECT TableName FROM DBC.TablesV WHERE DatabaseName = 'HSABC_DEV' AND (TableName LIKE 'Compliance%' OR TableName = 'CMS_ComplianceExceptionsAudit') ORDER BY 1;
```

---

## 1. Setup and configuration (CFG)

| ID | Test | Steps | Expected |
|---|---|---|---|
| CFG-01 | Configuration valid | `python -m compliance_framework.cli validate-config` on a worker | No ERROR except the known ones (Facets 210 until configured); WARNINGs read and accepted |
| CFG-02 | Unknown run type in crosswalk | Set a crosswalk `Run_Ty` to `'DLY,XYZ'`; validate | `XWALK_RUN_TYPE` error naming `XYZ`; revert |
| CFG-03 | Missing source config | Crosswalk row for a source with no file / internal config | `XWALK_NO_FILE_CONFIG` |
| CFG-04 | Period row with code and SQL | Set both `Rpt_Period_Cd` and `Rpt_Dt_Sql_Txt` on one `ComplianceRunTypePeriod` row | `RUN_TYPE_PERIOD`: "not both (the code is used)" |
| CFG-05 | Bad period code | `Rpt_Period_Cd = 'PREV_DAYS'` | `RPT_PERIOD` error |
| CFG-06 | Crosswalk row with code and SQL | Both on one crosswalk row | `RPT_PERIOD` "not both" |
| CFG-07 | Export statement checks | `Exp_Pre_Sql_Txt` with `{batch}`; `Exp_Post_Sql_Txt` with two statements; `Exp_Sql_Txt` an UPDATE | `EXPORT_CONFIG` errors: unknown placeholder, "one statement", "must be a SELECT" |
| CFG-08 | Staging ↔ core pairing shown | Validate with SNPCC1 (staging `Member…`, core `Enrollee…`) | Log line `staging -> core by column position: memberfirstname -> enrolleefirstname, …` |
| CFG-09 | Template overlap | Two file configs whose templates match the same name | `TEMPLATE_OVERLAP` |
| CFG-10 | Test connections | `test-connection` | Teradata and NAS OK; failures show the reason, no password |
| CFG-11 | Missing framework table | Rename `ComplianceFileLoad` (DEV only), run FILE_LOAD, rename back | Load `PROCESS_FAILURE` / step "COULD NOT RUN" with error 3807; retried; nothing half done |
| CFG-12 | Variables minimal | Open each Variable | Only `td_conn_var`, `meta_db`, `load_env`, `email_recipient`, `project` (+ `trigger_dags`, FILE_CHECK `settings`, FILE_RULES `pool`, EXPORT_LOAD `wait_for_triggered`) |

---

## 2. Batch creation (BC)

Trigger BATCH_CREATION (`{"project": "CMSUNIV"}` unless stated). Check with Q1 / Q2.

| ID | Test | Precondition / steps | Expected |
|---|---|---|---|
| BC-01 | Daily batches | Any day | One `…_<table>_DLY` batch per table, one row per crosswalk source listing DLY; report dates = previous day; `REQUEST_CREATED`; close date = request date (SLA 1); version `2200` |
| BC-02 | Batch ID format | BC-01 | `Btch_ID = <YYYYMMDD>_CMSUNIV_<table>_<run type>`; all sources share it |
| BC-03 | Rerun is safe | Trigger BC-01 again | No new rows (Q1 unchanged); report `created = 0` |
| BC-04 | Weekly on Sunday | `{"project": "CMSUNIV", "as_of": "<a Sunday>"}` | `…_WKLY` batches for internal-source tables only; dates = the 60 days ending the Saturday before; SLA 5 (closes Friday) |
| BC-05 | No weekly on other days | `as_of` a Monday | No WKLY batches |
| BC-06 | Monthly (MNT) | `as_of` = the Sunday before the 3rd Tuesday (e.g. 2026-10-18) | `…_MNT` batches, dates = previous calendar month (2026-09-01..09-30), SLA 5 |
| BC-07 | MNT not on other Sundays | `as_of` another Sunday | No MNT batches |
| BC-08 | Missed day caught up | Skip a day; run next day with `lookback_days: 2` | Missed day's DLY batches created with that day's dates (`Req_Dt_Key` = missed day) |
| BC-09 | Period override | `{"project":"CMSUNIV","run_type":"DLY","period":"PREV_N_DAYS(3)"}` | DLY batches with the last 3 days as dates |
| BC-10 | Scope | `{"project":"CMSUNIV","table_group":"SNPCC"}` | Only SNPCC1 batches |
| BC-11 | FA fixed dates | Run DLY | FA1 = last 14 days of previous month; FA2 = 01-01..01-31; FA3 = 11-01..12-31 previous year; FA4 = 11-01 previous year..01-31 |
| BC-12 | FA switch on Feb 1 | `as_of` 2027-01-31 then 2027-02-01 | FA2–FA4 dates move to the new year on 02-01 only |
| BC-13 | IN1 ad hoc with schedule | Any day | `…_SNPCC1_IN1` batch for source 11 only; held (not pulled / exported) |
| BC-14 | Crosswalk effective dates | Set a source's `Effective_End_Dt_Key` to yesterday | No new batch row for that source from today |
| BC-15 | Run type switched off for a source | Add a crosswalk row `Include_Ind = 0` for one run type from today | That source gets no batch of that run type from today; earlier batches untouched |
| BC-16 | Inactive run type | `Active_Ind = 0` on a run type | No batches of it; validate WARNING |
| BC-17 | Report dates by SQL | Period row with `Rpt_Dt_Sql_Txt` (e.g. 14 days before `{period_dt}`) | Batch dates from the SQL; SQL with values in the log |
| BC-18 | SQL returns no row | Make that SQL return nothing | No batch that day for that table; no error |
| BC-19 | Calendar batch | Insert a `ComplianceBatchCalendar` row for today | Batch with the calendar dates |
| BC-20 | Version per source | Crosswalk row of one source with version `2300` | That source's row has `Cmplnc_Vrsn = 2300` |
| BC-21 | Problems reported | Break a period code; run | Other batches still created; task fails (exit 1) with the error in the report; failure email |

---

## 3. Ad-hoc requests (IN)

Insert into `ComplianceRequestInTake`, then run BATCH_CREATION.

```sql
INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
VALUES ('CMSUNIV', 'ODAG3', '51', 'CMS', '2200', DATE '2026-09-01', DATE '2026-09-30', CURRENT_DATE, CURRENT_DATE);
```

| ID | Test | Steps | Expected |
|---|---|---|---|
| IN-01 | One-day request | Above | One `…_ODAG3_CMS` batch, source 51, the request's dates, SLA 1 |
| IN-02 | Multi-day request | `Req_End_Dt_Key = CURRENT_DATE + 2`; run 3 days (`as_of`) | One batch per day |
| IN-03 | No crosswalk needed | Source without a CMS crosswalk row | Batch still created |
| IN-04 | Missing config | Source with no file / internal config for the table | `INTAKE_FAILED` alert; no batch; `validate-config` `INTAKE_REQUEST` |
| IN-05 | Ad hoc held | Load the file | Loaded; SOURCE_LOAD / EXPORT_LOAD list it under `held` until released |
| IN-06 | Release | EXPORT_LOAD `{"project":"CMSUNIV","run_type":"CMS","release":"true"}` | Exported; `BATCH_RELEASED` on the timeline |
| IN-07 | Rerun | Run BATCH_CREATION again same day | No duplicate |

---

## 4. File check (FC)

Put the file in the inbound folder, trigger FILE_CHECK. Nothing is loaded. FILE_CHECK has `CHECK_MOVE_FAILED = true`.

| ID | Test | File | Expected |
|---|---|---|---|
| FC-01 | Good file passes | GOOD | Report file `PASSED`; `FILE_CHECK_PASSED` (not emailed); file stays; FILE_LOAD triggered |
| FC-02 | Header | BAD_HEADER | `FAILED`, "header is not the expected one: column n"; error log in `ErrorLog/`; file moved to `Error/`; task fails; FILE_LOAD not triggered |
| FC-03 | Trailer count | BAD_COUNT | `FAILED` trailer count |
| FC-04 | Records | BAD_RECORD | Column count and blank line findings with line numbers |
| FC-05 | Values | BAD_VALUE | `VALUE_IN` / `DATE_FORMAT` / `LENGTH` finding naming the field |
| FC-06 | Name | BAD_NAME | `FILE_REJECTED_UNPARSEABLE` / `INVALID_TOKEN` |
| FC-07 | No batch | NO_BATCH | "no batch exists for this period" |
| FC-08 | Still arriving | Copy a file and run within 120 s | File skipped ("still arriving"), checked next run |
| FC-09 | Keep the chain going | FILE_CHECK Variable `"fail_on_problems": false`; one good + one bad file | Task succeeds; FILE_LOAD still triggered and loads the good file |
| FC-10 | Case | File name in other case, record types lower case | Passes (nothing is case sensitive) |

---

## 5. File load – staging and core (FL)

Trigger FILE_LOAD. Check Q4 (load), Q6 (staging), Q7 (core), Q3 (timeline), file location on the NAS, control report.

| ID | Test | Steps | Expected |
|---|---|---|---|
| FL-01 | Good file | GOOD for an open DLY batch | Load `FILE_LOADED`, `Stg_Rcd_Cnt` = data rows; batch `SOURCE_DATA_READY` / `NEW_FILE`; core rows = data rows; file in `Archive/`; control report line `ACCEPTED`; timeline `FILE_RECEIVED`, `FILE_LOADED` |
| FL-02 | Derived columns | FL-01 | Staging / core: `Btch_ID`, `Run_Ty = DLY`, `Universe_Vrsn = 2200`, `Src_Data_Key` = source, `FileName`, `AuditDataID` = Load_ID, `StagingCreateDate` = load date, `Load_Dtts` = load time, row ID continuing from the highest |
| FL-03 | Type conversion to core | Core column `TIMESTAMP(0)` / `DATE` / `DECIMAL`, staging text | Core values typed; `StagingCreateDate` in a TIMESTAMP column = `YYYY-MM-DD 00:00:00`; empty text → NULL |
| FL-04 | Bad type | BAD_TYPE | `REJECTED` `CORE_TYPE_MISMATCH` naming the column and type (no value shown); staged rows removed; core untouched; file in `Error/`; task fails, alert |
| FL-05 | NOT NULL gap in core | Core NOT NULL column with no staging column | `REJECTED` `CORE_TYPE_MISMATCH` "core column X is NOT NULL but gets no value" |
| FL-06 | Names differ, same order | SNPCC1-style staging `Member…` / core `Enrollee…` | Core filled by position; log shows the pairs |
| FL-07 | Empty file | EMPTY | Load `FILE_LOADED`, `Stg_Rcd_Cnt = 0`; batch `SOURCE_DATA_READY_NO_DATA` / `NO_DATA`; file archived |
| FL-08 | Replace before close | GOOD again (new timestamp) for the same batch / source | New load `FILE_LOADED`; earlier load `RELOADED`; core holds only the new rows for that source; other sources untouched; `FILE_REPLACED_BEFORE_CLOSE` |
| FL-09 | Two sources, one batch | Files of sources 51 and 140 for the same DLY batch | Both in core under one `Btch_ID`; each source's rows separate |
| FL-10 | Staging per source and run type | FL-09, then a pull for the same table / run type | Staging keeps each source's rows (Q6); the next file of 51 replaces only 51's DLY rows |
| FL-11 | Same content other batch | DUP renamed to another batch's dates | Loaded; `FILE_SAME_CONTENT_OTHER_BATCH` alert |
| FL-12 | Batch closed | GOOD for a closed batch | `REJECTED` `FILE_REJECTED_BATCH_CLOSED`; file in `Error/` |
| FL-13 | Rejected by check | BAD_VALUE with `CHECK_ON_LOAD` or a staging type error | `REJECTED` (`FILE_CONTENT_CHECK_FAILED` / `FILE_PARSE_ERROR` with line); error log holds the record |
| FL-14 | Staging table busy | Start SOURCE_LOAD and FILE_LOAD for the same staging table together | One waits; if more than 30 s: `LOAD_INCOMPLETE` (`Reject_Rsn_Cd = STAGING_TABLE_BUSY`), loaded by the next run; no error |
| FL-15 | Stale staging lock | Leave a lock row older than 30 min (Q10) | `STAGING_LOCK_STALE` alert with the `release-lock` command; files wait |
| FL-16 | Move failure | Remove delete rights on the inbound folder; load | Loaded once; `FILE_MOVE_FAILED` emailed once; next runs only retry the move (no second load) |
| FL-17 | System failure | Stop Teradata access mid-run (or wrong password) | `PROCESS_FAILURE`; task retried after 5 min; file stays; no partial core |
| FL-18 | Unsupported type | `.xlsx` file | `FILE_TYPE_NOT_SUPPORTED` |
| FL-19 | Scope | `{"project":"CMSUNIV","table":"ODAG3"}` | Only ODAG3 files loaded |
| FL-20 | Archive by year | `"settings":{"ARCHIVE_BY_YEAR":true}` in a trigger | File in `Archive/<year>/` |
| FL-21 | One file from trigger | `{"project":"CMSUNIV","file":"<name>"}` | Only that file |
| FL-22 | Chain | FILE_LOAD succeeds | OVERRIDE_DECISIONS and EXPORT_LOAD triggered; with a rejected file the task fails and they are not |

---

## 6. Internal sources – SOURCE_LOAD (SL)

Trigger SOURCE_LOAD (`{"table_group": "SNPCC"}` to limit). Check the SQL log lines, Q4, Q6, Q7.

| ID | Test | Steps | Expected |
|---|---|---|---|
| SL-01 | Pull with rows | Source rows exist for the batch | Log: `Src_Sql_Txt on SQL_SNPCC`, SQL with `?`, parameters, "with values" copy; `FILE_LOADED`; core rows; batch `SOURCE_DATA_READY` |
| SL-02 | Driver chosen | No `driver` in the Connection Extra | Log `uses ODBC driver ODBC Driver 17 for SQL Server` (newest installed) |
| SL-03 | Empty before close | No source rows, close date later | `NO_DATA_YET`, report `WAITING`, pulled again next run |
| SL-04 | Empty on close date | No rows on the close date (DLY) | `FILE_LOADED` 0 rows, batch `SOURCE_DATA_READY_NO_DATA`; still pulled again until closed |
| SL-05 | Late source data | SL-04, then load source rows, rerun | Rows pulled; batch `SOURCE_DATA_READY` |
| SL-06 | Connection failure | Wrong host in the Connection | `REJECTED` `SOURCE_QUERY_FAILED` "could not connect to SQL_SNPCC: [HYT00] …"; incident recorded |
| SL-07 | Query failure | Wrong column in `Src_Sql_Txt` | "the query on SQL_SNPCC failed: [42S22] … Invalid column name '…'"; data values masked |
| SL-08 | Names not in staging | Column not aliased to a staging name | `SOURCE_DATA_REJECTED` listing the names |
| SL-09 | Source value wins | Query returns the ID column that has a `ROW_SEQUENCE` row | ID from the source |
| SL-10 | Framework columns protected | Query returns `Src_Data_Key` | Rejected: "the batch, source or load column, which the framework always fills" |
| SL-11 | Ad hoc held | IN1 / CMS batch | Under `held`; not pulled until `release` |
| SL-12 | Same Teradata system | Internal source with `Src_Db_Ty = TERADATA`, no connection | INSERT … SELECT on Teradata; rows loaded |
| SL-13 | Type conversion / NOT NULL | Pull into a typed core, or a NOT NULL gap | As FL-03 / FL-05 (`CORE_TYPE_MISMATCH`) |
| SL-14 | Concurrent with FILE_LOAD | SL-01 while FILE_LOAD loads the same staging table | Both succeed (one waits); each source keeps its staging rows |

---

## 7. Overrides (OV)

```sql
INSERT INTO HSABC_DEV.ComplianceBatchOverride (Override_Ty, Req_ID, Btch_ID, Reuse_Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By)
VALUES ('<type>', <Req_ID>, '<batch>', '<earlier batch or NULL>', 'APPROVED', CURRENT_DATE + 7, 'test', '<you>');
```

| ID | Test | Steps | Expected |
|---|---|---|---|
| OV-01 | Carry-forward (CMS) | Open CMS batch without data; `REUSE` with an earlier closed CMS batch; run OVERRIDE_DECISIONS | Batch `SOURCE_DATA_CARRY_FORWARD`, `Reuse_Btch_ID` set; `OVERRIDE_APPROVED`; export copies the reused rows under the new batch ID |
| OV-02 | Carry-forward refused | `REUSE` on a DLY batch (`Carry_Fwd_Ind = 0`) or on a closed batch | `OVERRIDE_INVALID_DETECTED` with the reason; task succeeds |
| OV-03 | Real file after carry-forward | OV-01, then a GOOD file | File replaces the carried data; `CARRY_FORWARD_REMOVED` |
| OV-04 | Late arrival | Closed `DATA_NOT_PROVIDED` batch; `LATE_ARRIVAL`; load GOOD | Loaded; batch stays closed, becomes `REQUEST_COMPLETE`; `LATE_ARRIVAL_LOADED` |
| OV-05 | Correction | Closed `REQUEST_COMPLETE` batch; `CORRECTION`; load GOOD | Data replaced; `CORRECTION_LOADED`; re-exported |
| OV-06 | Reopen | `REOPEN` on a closed batch; run OVERRIDE_DECISIONS | Batch open again; `BATCH_REOPENED` alert; closes again after new data |
| OV-07 | Expired | `Valid_Thru_Dt_Key` yesterday | Not used; `OVERRIDE_EXPIRED` |
| OV-08 | Not approved | `Apprvl_Stat = PENDING_REVIEW` | Ignored |

---

## 8. Export load and extract (EX)

Trigger EXPORT_LOAD. Check Q8, the report (`batches`: status, `data_from`, rows), timeline `EXPORT_LOADED`, the export
feed views (extract).

| ID | Test | Steps | Expected |
|---|---|---|---|
| EX-01 | Not due yet | Open WKLY batch before its close date | Not exported (or listed held); report `NO BATCHES TO EXPORT` |
| EX-02 | Due on close date | DLY batch with data (SLA 1) | Exported the same day; `EXPORT_LOADED`; Q8 rows = core rows; `Btch_ID` = batch |
| EX-03 | Procedure mode | A test table with `Exp_Proc_Nm` set (CMSUNIV itself has no procedure) | Log `procedure <db>.<proc>` with parameters; rows stamped `Btch_ID` (and `Exp_Btch_ID` if present) |
| EX-04 | All sources | Batch with several sources | Each source exported; one `EXPORT_LOADED` per source row |
| EX-05 | Re-export replaces | Reload a file (FL-08), export again | Old rows of that source removed, new inserted; no duplicates |
| EX-06 | Only changed batches | Rerun EXPORT_LOAD without changes | Nothing exported again |
| EX-07 | Newest batch of the period | Two batches same dates; later one `MISSING` | The later one `SKIPPED` ("the period's data is batch …"); data of the earlier one kept |
| EX-08 | No data batch | `NO_DATA` batch | Earlier export rows of the period removed, nothing inserted |
| EX-09 | Carry-forward copy | OV-01 batch | `data_from` = reused batch; rows inserted under the new batch ID; reused batch's rows kept |
| EX-10 | Ad hoc only when released | CMS batch | Held; with `release: true` exported |
| EX-11 | Hold | `{"hold": "WKLY"}` | WKLY batches held |
| EX-12 | Release time | `"settings":{"RELEASE_TIME":"20:00"}` | Due batches exported only from 20:00 on the close date |
| EX-13 | Named batch | `{"project":"CMSUNIV","batch":"<batch>"}` | Only that batch, even if exported before |
| EX-14 | Copy mode | CMSUNIV export rows as delivered (no procedure) | Core columns copied into `SDO_CMSUNIV_<table>_2022` by name, then value code, then order; `Btch_ID` = batch; validate lists the pairs and warns about feed columns no core column fills |
| EX-15 | Custom SELECT | `Exp_Sql_Txt` reading core with `{data_btch_id}` / `{src_id}` and aliases | Rows from the SELECT; batch, `Exp_Btch_ID` and source columns stamped by the framework even if the SELECT returns its own; SQL with values in the log |
| EX-16 | SELECT column not in export | Alias to a non-existing column | Batch `FAILED`: "returns columns that are not in …"; `EXPORT_LOAD_FAILED` |
| EX-17 | Pre / post once per batch | `Exp_Pre_Sql_Txt` inserting a log row, `Exp_Post_Sql_Txt` updating the batch's rows | Pre and post run once per batch (not per source), in order, logged with values |
| EX-18 | Rollback | Make post fail (bad table) | Pre, every source's export and post rolled back; every source `FAILED`; old export rows still there; retried next run |
| EX-19 | Multiple core tables | ODAG3 sources 51/140 (file core) and 40 (AZ core) | Each source exported from its own core table into the one export table |
| EX-20 | Partial failure | Break one table's procedure | Other tables exported; task fails at the end; FILE_RULES not triggered on that run |
| EX-21 | Extract / feed view | After EX-02 | `SELECT COUNT(*) FROM EXPORT_FEED_DEV_V.SDO_CMSUNIV_<table>_2022 WHERE Btch_ID = '<batch>'` = Q8 total; columns, formats and values as the CMS layout requires; no rows of other batches mixed |
| EX-22 | Extract per run type | Batches of DLY and MNT exported | Feed shows each batch separately by `Btch_ID` / `Run_Ty` |
| EX-23 | Wait for rules | EXPORT_LOAD with `wait_for_triggered` | Task `trigger_…file_rules` deferred until FILE_RULES ends; EXPORT_LOAD ends after it; next EXPORT_LOAD waits |

---

## 9. Data quality rules – FILE_RULES (RU)

Runs after EXPORT_LOAD (or trigger FILE_RULES with `{"project":"CMSUNIV"}`). Check the task log, Q4 `Rules_Stat`, Q9.

| ID | Test | Steps | Expected |
|---|---|---|---|
| RU-01 | Rules per exported batch | EX-02 for CDAG2 MNT | Log `FILE_RULES | <batch> | rule group(s) CDAG2/MNT`, `GRE rule_group=CDAG2 rule_variant=MNT run_key=<batch> extra_filters={'Btch_ID': '<batch>'}` |
| RU-02 | All sources at once | Batch with several sources | One GRE call per batch; rules see every source's rows of the batch only |
| RU-03 | Rule SQL logged | RU-01 | Each rule's final SQL (with `$env` and the batch filter) at INFO |
| RU-04 | Pass | Clean data | `Rules_Stat = PASSED` on the batch's loads; `FILE_RULES_PASSED` |
| RU-05 | Fail | Data breaking a rule | `FAILED`; `FILE_RULES_FAILED`; `RULES_VALIDATION_FAILED` emailed; data and export unchanged; task succeeds |
| RU-06 | Results keyed by batch | RU-04 / 05 | Q9: `gre_results.run_key` = batch; `gre_exceptions.run_type` = run type, `batch_schedule` = batch |
| RU-07 | Not exported yet | Load without export (table has an export) | Listed `waiting_for_export`; rules not run |
| RU-08 | No rules | Table / run type with no active rules | `SKIPPED` |
| RU-09 | No data | Batch with 0 rows | `SKIPPED`; GRE not called |
| RU-10 | Rule error | Break one rule's SQL | `ERROR`; `RULES_ENGINE_TECHNICAL_FAILURE` emailed once; other batches still run; task fails at the end; retried next run |
| RU-11 | Rerun after reload | Reload + re-export a batch | Rules run again with the same `run_key`; results refreshed (no duplicates) |
| RU-12 | Variant empty | Rules with empty `RULE_VARIANT` | Run for every run type |
| RU-13 | Binding override | `ComplianceRuleBinding` row naming another group | That group runs instead |
| RU-14 | One at a time | Two FILE_RULES runs at once | Second waits for the `gre_rules` pool slot |
| RU-15 | Per table group | FILE_RULES Variable `runs` per table group | One task per group in the same DAG run |

---

## 10. Batch close (CL)

Trigger BATCH_CLOSE on / after the close date. Check Q1 / Q2 / Q5.

| ID | Test | Batch before | Expected |
|---|---|---|---|
| CL-01 | With data | `SOURCE_DATA_READY` | `REQUEST_COMPLETE` / `NEW_FILE`, `Batch_Close_Ind = 1`, `BATCH_CLOSED` |
| CL-02 | No data from source | `SOURCE_DATA_READY_NO_DATA` | `DATA_NOT_PROVIDED` / `NO_DATA`; no alert |
| CL-03 | Nothing arrived | `REQUEST_CREATED` | `DATA_NOT_PROVIDED` / `MISSING`; `SOURCE_MISSING_AT_CLOSE` emailed |
| CL-04 | Carried forward | `SOURCE_DATA_CARRY_FORWARD` | `REQUEST_COMPLETE` / `CARRY_FORWARD` |
| CL-05 | Not yet due | WKLY before Friday | Stays open |
| CL-06 | Busy batch | Close while a load holds the batch | Stays open, `BATCH_CLOSE_DEFERRED_LOCKED`; closed next run |
| CL-07 | Hold | `{"hold":"WKLY"}` | WKLY stays open |
| CL-08 | IN1 | IN1 batch next day | `DATA_NOT_PROVIDED` / `MISSING`, one alert a day |
| CL-09 | Chain | BATCH_CLOSE succeeds | BATCH_CREATION and NOTIFY triggered |
| CL-10 | Manual close | `close-batch --btch-id <batch> --closed-by <you>` (optionally `--src-id`) | Closed; recorded with your name |

---

## 11. Notifications (NT)

| ID | Test | Steps | Expected |
|---|---|---|---|
| NT-01 | Alerts sent | After cases raising alerts, run NOTIFY | One email per unsent event to `email_recipient`, subject `[ERROR] <Event_Ty>`, body with ids and codes (no member data); `Notified_Ind = 1` (Q11 empty) |
| NT-02 | Not emailed events | `FILE_CHECK_PASSED`, `BATCH_CLOSE_DEFERRED_LOCKED` | Not emailed |
| NT-03 | Send failure | Break `smtp_default` | Events stay unsent; sent on the next run after the fix |
| NT-04 | All projects | NOTIFY Variable without `project` | Events of every project (and of none) |
| NT-05 | DAG emails | Any DAG success / failure | `success_email` / `fail_email` to `email_recipient` with the environment in the subject; failure also opens a ServiceNow incident |

---

## 12. Airflow behaviour (AF)

| ID | Test | Steps | Expected |
|---|---|---|---|
| AF-01 | Problem = no retry | Rejected file (FL-04) | Task fails at once (`AirflowFailException`), no retry, incident + email |
| AF-02 | Technical = retry | Teradata down | Task retried once after 5 minutes, then fails |
| AF-03 | One run at a time | Trigger a DAG twice | Second run queued until the first ends |
| AF-04 | Trigger keys | Trigger with `{"meta_db":"X","settings":{"FILE_STORE":"local"}}` | Ignored with a warning ("not taken from the trigger") |
| AF-05 | `as_of` | `{"as_of":"2026-10-18"}` | Step runs as of that day |
| AF-06 | Passed on | FILE_CHECK `{"table":"ODAG3"}` | Triggered FILE_LOAD gets `table = ODAG3` |
| AF-07 | Deferred wait | EX-23 | Wait shown as deferred (no worker slot) |
| AF-08 | Pool | FILE_RULES task | Runs in pool `gre_rules` |
| AF-09 | Timed runs | `runs` with `name` + `schedule_interval` | Each scheduled time runs its entry; `{"run":"MNT"}` runs that entry by hand |
| AF-10 | Missing Variable | Delete a Variable (DEV) | DAG still listed; task fails with "Variable … is not set" |
| AF-11 | LOCK_TTL raised | `execution_timeout_minutes` above `LOCK_TTL_MINUTES` | Log "LOCK_TTL_MINUTES raised" |
| AF-12 | Locks released | After any run | Q10 has no leftover locks of finished runs |

---

## 13. Admin commands (AD)

| ID | Command | Expected |
|---|---|---|
| AD-01 | `validate-config` | Full list of issues |
| AD-02 | `health` | Loads in progress for long, rejected counts by reason |
| AD-03 | `locks` / `release-lock --key <key>` | Lists locks; removes the given one |
| AD-04 | `close-batch`, `close-batches` | Closes as CL-10 |
| AD-05 | `run --module FILE_RULES --project CMSUNIV` | Same as the DAG step |
| AD-06 | `show-config`, `list-modules`, `test-connection` | Output without passwords |

---

## 14. Logging and security (LG)

| ID | Test | Expected |
|---|---|---|
| LG-01 | No passwords in logs | Search task logs for the passwords: none |
| LG-02 | No member data in logs, reports, alerts | Errors show column names, never values (`'***'`); error logs in `ErrorLog/` hold the rejected records (restricted folder) |
| LG-03 | SQL logging | Every configured SQL (source queries, SQL columns, report-date SQL, export procedure / SELECT / pre / post, rules) logged with parameters and a copy with values |
| LG-04 | Progress lines | `FILE_LOAD | <file> | load n | …` lines for each step of a file |
| LG-05 | Step report | JSON report at the end of every task |

---

## 15. End-to-end scenarios (E2E)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| E2E-01 | A normal day (DLY) | BATCH_CREATION → GOOD files for all FDR sources → FILE_CHECK → FILE_LOAD → SOURCE_LOAD → EXPORT_LOAD (→ FILE_RULES) → BATCH_CLOSE (→ BATCH_CREATION, NOTIFY) | Every DLY batch `REQUEST_COMPLETE`; export and feed hold every source; rules results per batch; alerts only for real problems |
| E2E-02 | Monthly cycle (MNT) | `as_of` the Sunday before the 3rd Tuesday; files for MNT; export after the close date (SLA 5) | MNT batches with the previous month; exported on the close date; closed |
| E2E-03 | Weekly internal | `as_of` a Sunday; SOURCE_LOAD daily until data; Friday close | WKLY pulled again until data; exported at close date; rules run |
| E2E-04 | Missing source | No file for one source | That source `DATA_NOT_PROVIDED` / `MISSING` + alert; other sources complete; export has the others |
| E2E-05 | Bad then corrected file | BAD_VALUE, then GOOD before close | First `REJECTED` (in `Error/`), second loaded; batch complete |
| E2E-06 | Late file after close | LATE_ARRIVAL override then file | Data loaded and exported; batch `REQUEST_COMPLETE` |
| E2E-07 | Ad-hoc CMS request with carry-forward | Intake, no file, REUSE, release, export | Exported with the reused data under the CMS batch ID |
| E2E-08 | Table group export + rules | EXPORT_LOAD `{"table_group":"ODAG"}` | One FILE_RULES run covering ODAG1–6 batches one at a time; EXPORT_LOAD ends after it |
| E2E-09 | Rerun the whole day | Repeat E2E-01 steps | No duplicates anywhere (batches, core, export, alerts) |

---

## 16. Upgrade statements (UP)

For an installation created before the version shown (runbook section 16).

| ID | From before | Check after running the statements |
|---|---|---|
| UP-01 | 0.3.57 | `ComplianceRunTypePeriod` has `Rpt_Dt_Sql_Txt`; `Rpt_Period_Cd` nullable |
| UP-02 | 0.3.60 | `ComplianceFileLoad.Reject_Rsn_Cd` exists; no `QUARANTINED` rows |
| UP-03 | 0.3.62 | No `PROMOTED` / `SUPERSEDED` / `FAILED_TECHNICAL` / `DEFERRED` load statuses |
| UP-04 | 0.3.63 | No `FILE_PROMOTED` / `…_PROMOTED` timeline events |
| UP-05 | 0.3.67 | `ComplianceExportConfig` has `Exp_Sql_Txt`, `Exp_Pre_Sql_Txt`, `Exp_Post_Sql_Txt` |
| UP-06 | Run type rename | Old `DAILY` / `WEEKLY` inactive; `DLY` / `WKLY` in periods and crosswalk; old batches closed under their old IDs |

---

## Sign-off

| Area | Cases | Passed | Failed | Blocked | Tester | Date |
|---|---|---|---|---|---|---|
| CFG | 12 | | | | | |
| BC | 21 | | | | | |
| IN | 7 | | | | | |
| FC | 10 | | | | | |
| FL | 22 | | | | | |
| SL | 14 | | | | | |
| OV | 8 | | | | | |
| EX | 23 | | | | | |
| RU | 15 | | | | | |
| CL | 10 | | | | | |
| NT | 5 | | | | | |
| AF | 12 | | | | | |
| AD | 6 | | | | | |
| LG | 5 | | | | | |
| E2E | 9 | | | | | |
| UP | 6 | | | | | |
