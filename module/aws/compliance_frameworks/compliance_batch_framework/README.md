# compliance_batch_framework — AWS deployment

Runs the compliance batch framework (the `cms-compliance-framework` Python package) on AWS:

```
EventBridge scheduled rules (UTC)               one rule per schedule entry
   ODR  batches        12:00 UTC   {steps: [BATCH_CREATION]}   (what is due: the metadata tables)
   ODR  file_load      every 15'   {steps: [FILE_LOAD, OVERRIDE_DECISIONS, NOTIFY]}
   ODR  close          hourly      {steps: [BATCH_CLOSE, NOTIFY]}
        │                                        manual: src/run_workflow.sh dev ODR FILE_LOAD,BATCH_CLOSE
        ▼
Step Functions  compliance_batch_framework_ODR_dev      one workflow per project (+ all_projects)
   validate the steps -> run them one at a time -> on any failure stop and email the project
        │  one Glue run per step
        ▼
Glue  compliance_batch_framework_runner_dev             ONE job for every project and module
   framework run --module <STEP> --project ODR [--run-type --period --table --as-of]
        │
        ▼
existing RDS PostgreSQL (VPC)  +  S3 inbound (with Archive/ and Error/)  +  SES email
```

- **Two Glue jobs in total.** `runner` runs every module for every project (one module, one project per
  run); `metadata_load` loads configuration CSVs you drop in S3.
- **One Step Functions workflow per project**, all generated from one template. An execution runs the
  steps named in its input, in order; the first failure stops it.
- **Schedules are separate from the workflow.** Each schedule has its own time and input
  (steps, run type, period), so each step can run at its own time, and monthly / weekly / daily runs share
  the workflow. Any steps can be run by hand at any time.

Deployed as two Terragrunt submodules by the team's GitHub Actions workflow, each with its own
`env-config/us-east-1/` (`common.tfvars` + `<env>.tfvars`). Every resource name starts with `compliance`;
every resource carries the `required_common_tags` of `common.tfvars` plus `Environment` and `ManagedBy`
(per-project resources add `Project`).

## Steps

| Step | Framework module | Default Glue size / timeout | A "completed with problems" result… |
|---|---|---|---|
| `BATCH_CREATION` | the scheduled batches due today (`Batch_Schedule_Sql_Txt` of the run type, dates from the crosswalk's `Rpt_Dt_Sql_Txt`), and the project's ad-hoc intake requests | 0.0625 DPU / 60 min | stops the workflow (configuration problem) |
| `FILE_CHECK` (optional, in no default schedule) | read-only check of the files waiting in the project's inbound folders | 1 DPU / 60 min | stops the workflow: a waiting file has a problem |
| `FILE_LOAD` | every file waiting in the project's inbound folders | 1 DPU / 120 min | continues: a bad file is audited, retried next sweep and emailed once |
| `FILE_RULES` | the rules of every loaded file that has not had them yet (separate execution, after the load) | 0.0625 DPU / 120 min | a failed rule is audited and emailed; stops only when the rules engine cannot run |
| `OVERRIDE_DECISIONS` | apply / expire approved REUSE overrides | 0.0625 DPU / 30 min | continues: an invalid override is audited and emailed once |
| `BATCH_CLOSE` | close batches past their SLA hold that have data or are in exception (`run_type` / `table` narrow it) | 0.0625 DPU / 30 min | stops the workflow |
| `NOTIFY` | email the project's pending events | 0.0625 DPU / 30 min | stops the workflow |

A framework error (exit 2: database unreachable, bad configuration, unknown period) always stops the
workflow. Sizes, timeouts and the "problems" behaviour are `step_settings`. Every step is idempotent and
safe to run concurrently with itself — see *Fallbacks*.

## Configuring projects and schedules

Projects and their schedules are `local.projects` in `stepfunctions/_local.tf` (the same in every
environment); who is emailed is `project_alert_emails` in `stepfunctions/env-config/us-east-1/<env>.tfvars`.

```hcl
projects = {
  UNIVERSE = {
    settings = { FILE_RULES_MODE = "ANNOTATE" }                # framework settings for this project only
    schedules = {
      batches         = { expression = "cron(0 12 * * ? *)",  steps = ["BATCH_CREATION"] }   # daily; the metadata decides what is due
      file_load       = { expression = "cron(0/15 * * * ? *)", steps = ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"] }
      close           = { expression = "cron(30 4 * * ? *)",   steps = ["BATCH_CLOSE", "NOTIFY"] }
    }
  }
  all_projects = {                                             # steps run without --project
    schedules = { notify_unscoped = { expression = "cron(0 * * * ? *)", steps = ["NOTIFY"] } }
  }
}
```

- `expression`: `cron(min hour day month weekday year)` or `rate(15 minutes)`, **in UTC** (EventBridge
  rules have no time zone). Chicago is UTC-5 in summer and UTC-6 in winter:

  | Wanted (Chicago) | UTC expression | Runs at |
  |---|---|---|
  | 06:00 daily | `cron(0 12 * * ? *)` | 07:00 CDT / 06:00 CST |
  | 06:00 on the 1st | `cron(0 12 1 * ? *)` | same, on the 1st in both |
  | 06:00 Monday | `cron(0 12 ? * MON *)` | same, on Monday in both |
  | 23:30 daily | `cron(30 4 * * ? *)` | 23:30 CDT / 22:30 CST |
  | every 15 minutes / hourly | `cron(0/15 * * * ? *)` / `cron(0 * * * ? *)` | unaffected |

  Keep daily times out of 05:00–06:59 UTC (Chicago midnight) so the run's business date never changes
  with daylight saving. One-time runs: use `src/run_workflow.sh`.
- Optional per schedule (normally left out: the metadata decides, see *Scheduled batches*): `run_type`, `period`, `table`,
  `as_of`, `enabled = false`. A project with `enabled = false` keeps its workflow for manual runs.
- Adding a project = adding an entry to `local.projects` (and its emails to `project_alert_emails`) and
  deploying `stepfunctions`: a new workflow, schedule rules and alert topic; no new Glue job.
- Keep the `all_projects` hourly `NOTIFY`: it emails events that belong to no project (e.g.
  `CONFIG_VALIDATION_FAILED`). `plan` warns if it is missing.

## Metadata database and schema

Both are inputs of the Glue jobs, set per environment in `glue/env-config/us-east-1/<env>.tfvars`:

| tfvars | Glue job argument | Meaning |
|---|---|---|
| `metadata_schema` | `--FRAMEWORK_METADATA_SCHEMA` (runner), `--METADATA_SCHEMA` (metadata_load) | PostgreSQL schema holding the framework tables (default `cms_compliance`) |
| `rds_database_name` | `--FRAMEWORK_DB_NAME` (runner), `--RDS_DATABASE_NM` (metadata_load) | PostgreSQL database; `null` = the `dbname` of the secret |

They can also be given for one run: `metadata_schema` / `metadata_db` in a workflow's input
(`run_workflow.sh --metadata-schema S --metadata-db DB`, or a schedule entry of `local.projects`), or the
job arguments above on a direct Glue run. A run's value wins over the project's settings and the job
default. `metadata_load` adds the schema to a `--TABLE_NAME` that has none.

## Where files go after a run

Every file leaves the inbound folder once it has been processed, into a subfolder of that same folder:

| Outcome | Moves to |
|---|---|
| Loaded to core | `<inbound folder>/Archive/` |
| Not loaded: unknown name, no batch or a closed batch, wrong column count, bad characters, an empty file where one is not allowed | `<inbound folder>/Error/` |
| Technical failure | stays in place and is retried by the next sweep |

- **Every pickup is a new load.** Each time FILE_LOAD finds a file in the inbound folder it gets a new row in
  `ComplianceFileLoad` (a new `Load_ID`) and is processed from the start, whatever happened to a file of the same
  name before: the same name and path may come back with updated data, after a rejection or after a load. For an
  open batch it replaces the batch's data; a closed batch still needs the usual override. Earlier rows stay as
  history. A load of the same file left unfinished by a run that stopped is closed as `FAILED_TECHNICAL`.
- There is no quarantine path and no archive path to configure: `s3://bucket/odr/in/X.txt` goes to
  `s3://bucket/odr/in/Archive/X.txt` or `s3://bucket/odr/in/Error/X.txt`. Sweeps are not recursive, so
  the two subfolders are never picked up again. A name already there gets `_1`, `_2`, ... before the extension
  (`X_1.txt`), and its error log follows (`ErrorLog/X_1_errors.txt`); nothing is replaced.
  The schema keeps every pickup of an object (index `ix_compliancefileload_object`, not unique); on an existing
  database: `DROP INDEX uq_compliancefileload_object; CREATE INDEX ix_compliancefileload_object ON
  ComplianceFileLoad (S3_Bucket, S3_Key);`.
- The folder names are the `ARCHIVE_FOLDER` and `ERROR_FOLDER` settings (defaults `Archive`, `Error`). With
  `ARCHIVE_BY_YEAR = true` a loaded file goes to `Archive/<year>/`.
- The reason for a rejection is in `ComplianceFileLoad.Quarantine_Rsn_Cd` / `Error_Txt` and in the email.

## Sample metadata

A complete worked example - every metadata row for one project (three FDR and two internal sources; daily,
monthly and ad-hoc run types; file checks; an existing table layout) as PostgreSQL `INSERT` statements - is in
`src/docs/examples/cmsuniv_odag3_sample_metadata_postgres.sql`.

## File names (`Src_File_Nm_Tmplt`)

How a project's files are named is not in the code: each `ComplianceSourceFileConfig` row describes its
file name in `Src_File_Nm_Tmplt`, as fixed text plus tokens. `FILE_LOAD` and `FILE_CHECK` use the same rule.

| Token | Meaning |
|---|---|
| `{RUNTY}` | The run type of the file; must be a run type configured for the table and source. Required, once |
| `{RPTSTART}`, `{RPTEND}` | The report dates, `YYYYMMDD` unless a format is given. Required, once each |
| `{RPTMONTH}` | Instead of the two dates: one report month (`YYYYMM`), meaning its first to its last day |
| `{TS}` | The file's timestamp, 12 or 14 digits (`YYYYMMDDHHMM[SS]`) unless a format is given. Optional |
| `{VERSION}` | Must equal the row's `File_Vrsn_Cd` (the version code as it is written in file names) |
| `{PROJECT}`, `{TABLE}`, `{SRC}` | The row's `Project_Cd`, `Table_Nm`, `Src_ID` |
| `{ANY}` | Any text, ignored (a sequence number, a vendor reference) |

- **Other date formats:** write the format after the token: `{RPTSTART:MMDDYYYY}`, `{RPTEND:YYYY-MM-DD}`,
  `{RPTMONTH:MMYYYY}`, `{TS:YYYYMMDD}`. Formats are built from `YYYY`, `YY`, `MM`, `DD`, `HH`, `MI`, `SS`
  and separators such as `-` or `.` (`MM` after `HH` means minutes).
- Two tokens that are read from the name must have fixed text between them, and the template ends with
  the file extension, which is the file type.
- Report dates and the run type are always required; a file is matched to its batch by them.
- A name that fits a row except for the version is rejected with
  `version '17' in the file name is not the configured version (19)`.

Examples:

| File name | `Src_File_Nm_Tmplt` | `File_Vrsn_Cd` |
|---|---|---|
| `CMSAuth_MedHOK_CD_19_MNT_20181226_20190124_201901171024.txt` | `CMSAuth_MedHOK_CD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt` | `19` |
| `CLAIMS_WKL_01052026_01112026.csv` | `CLAIMS_{RUNTY}_{RPTSTART:MMDDYYYY}_{RPTEND:MMDDYYYY}.csv` | |
| `ENR_MNT_202602_batch-7.txt` | `ENR_{RUNTY}_{RPTMONTH}_{ANY}.txt` | |

`validate-config` reports a template that is not valid (`TEMPLATE`) and two rows whose templates can
match the same name (`TEMPLATE_OVERLAP`).

## Optional file check before the load (`FILE_CHECK`)

`FILE_CHECK` looks at the files that are waiting in the inbound S3 locations and reports **every** problem of
each file in one pass, before anything is loaded. It reads each file where it is: no table is loaded, no
load is registered and no batch changes (a failed file is moved only with `CHECK_MOVE_FAILED`). `FILE_LOAD` does not depend on it and makes
its own checks; a project uses `FILE_CHECK` only when it wants the full list of problems up front, or
wants to hold the load until the files are clean.

| Check | Finding | What is checked |
|---|---|---|
| File name | `FILE_NAME` | Matches exactly one `Src_File_Nm_Tmplt`; version, report dates and `{TS}` are valid; the file is in that config's inbound folder |
| Batch | `BATCH` | A batch exists for the table, source, run type and report dates, and is open (or has an approved override) |
| Encoding | `ENCODING` | The file is valid `FILE_ENCODING`; the first bad line is named |
| Structure | `MALFORMED`, `BLANK_LINE` | Quoting is well formed; no blank lines inside the file (blank lines at the end are ignored) |
| Header | `HEADER` | Present when `Src_File_Has_Hdr_Ind = 1`; the expected number of fields (see `COLUMN_COUNT`); the names, with a `HEADER_NAMES` row |
| Record types | `RECORD_TYPE` | With a `RECORD_TYPE` row (for example `H,D,T`): the first field of the header, of every data row and of the trailer |
| Columns | `COLUMN_COUNT` | Every data row has the expected number of fields: the `COLUMN_COUNT` row, or else the number of names in the `HEADER_NAMES` row; with neither row the count is not checked. The first lines are named |
| Trailer | `TRAILER` | Present when `Src_File_Has_Trlr_Ind = 1`; with a `TRAILER` row: field count, file name, row count, timestamp |
| Rows | `ZERO_RECORDS` | At least one data row, unless `Allow_Zero_Rcd_Ind = 1` |
| Source | `SOURCE` | The file's source is in `ComplianceSourceSystem` and active |

A file belongs to the batch of its project, table, source, run type and report dates. Nothing else is looked at: not `ComplianceDataSetSourceXwalk`, not its effective dates and not the day the batch was requested, so a file for an ad hoc batch or for a batch requested on another day matches the same way.

Only problems of the file are findings. A setup problem (staging table missing, wrong delimiter, a wrong `ComplianceFileCheckConfig` or `ComplianceTargetColumnConfig` row) is reported as an error of the step: the files of that source are not checked, get no error file and are not moved. `validate-config` lists the same problems.

`FILE_CHECK` does not read the staging or core table: what a file must look like comes from `ComplianceFileCheckConfig` only. The one exception is `COLUMN_VALUES`, which compares values with the staging columns and therefore reads the table when (and only when) that row is present.

**Which content checks a file gets** is in its own table, `ComplianceFileCheckConfig`, one row per check.
`ComplianceSourceFileConfig` only describes the file and where it loads.

| Column | Meaning |
|---|---|
| `Project_Cd` | The project of the files |
| `Table_Nm`, `Src_ID` | The file the row applies to; `*` = every table / every source of the project |
| `Check_Cd` | `RECORD_TYPE`, `HEADER_NAMES`, `COLUMN_COUNT`, `TARGET_COLUMNS`, `TRAILER`, `CHARACTER_SET` or `COLUMN_VALUES` |
| `Check_Val_Txt` | The value of the check (below) |
| `Include_Ind` | 1 = the check applies to the files the row covers; 0 = those files are excluded from the check |
| `Active_Ind` | 1 = the row is in use; 0 = the row is ignored, as if it were not there |

| `Check_Cd` | `Check_Val_Txt` | What is checked |
|---|---|---|
| `RECORD_TYPE` | `H,D,T` | Three codes: header, detail, trailer. The first field of each line must be the code of its position. Without this check the first line is the header and the last line the trailer by position (`Src_File_Has_Hdr_Ind`, `Src_File_Has_Trlr_Ind`) |
| `HEADER_NAMES` | `Member Id\|Paid Amount\|Member Name` | The expected header line (with the file's delimiter, or comma separated). Names are compared in order, ignoring upper / lower case and surrounding spaces. Without this check only the number of header columns is compared with the staging table |
| `COLUMN_COUNT` | `23` | The number of fields in the header and in every data row, counted as delimiters + 1, so a record type field (`H` / `D`) counts. The trailer is not counted here (see `TRAILER`). |
| `TARGET_COLUMNS` | `MemberFirstName\|MemberID\|SourceUnique_ID` | Not a check: the staging column of each field of the file, in file order (the old `TargetColumnList`, without the derived columns). Use it when a source's file skips table columns or is in a different order. Field n goes to the n-th listed column; columns not listed and not in `ComplianceTargetColumnConfig` are loaded as NULL. A listed column must be a staging column the file can fill (not a column of `ComplianceTargetColumnConfig`, not `Btch_ID`). With `*` and per-source rows each source can have its own layout; `Include_Ind = 0` makes a source use the table order |
| `TRAILER` | `RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP` | What each trailer field holds, in order: `RECORD_TYPE`, `FILE_NAME` (must equal the file's name), `DATA_ROW_COUNT` (data rows) or `TOTAL_ROW_COUNT` (every line, header and trailer included), `TIMESTAMP` (`YYYYMMDD`, `YYYYMMDDHHMM` or `YYYYMMDDHHMMSS`), `SKIP` |
| `CHARACTER_SET` | `latin-1` | Every line must only contain characters of that character set (`latin-1`, `ascii`, `cp1252`, ...) and no control characters (tab is allowed). Use it when the target columns cannot hold every character: each offending line is reported with the character and the record |
| `COLUMN_VALUES` | `ALL`, or any of `LENGTH,NUMBER,REQUIRED` | Every value of every data row against its target staging column: `LENGTH` = not longer than a character column, `NUMBER` = a number for a numeric column, `REQUIRED` = not empty for a `NOT NULL` column. Every offending value is reported with its line, column and record, so all bad records are listed before the load |

- **Inactive rows are ignored.** `Active_Ind` only says whether a row is in use.
- **The most specific active row wins** for each check, whether it includes or excludes: the file's table and
  source, then its table (any source), then its source (any table), then `*`. So a row for one source always
  overrides a `*` row, in either direction: exclude one source from a check every other source gets, or include
  one source in a check the others do not get.
- Two active rows cannot collide at the same level, because a table, source and check can have only one row.
- **To exclude one file from a check**, add a row for that table and source with `Include_Ind = 0`.
- A file with no row for a check does not get that check. The name, batch, source, encoding, blank-line,
  column-count and zero-row checks always run and need no row.

Example: every file of the project must have the standard header; only the FDR source `51` has record
types and a trailer; source `20` has a different layout, so its header check is off.

| `Project_Cd` | `Table_Nm` | `Src_ID` | `Check_Cd` | `Check_Val_Txt` | `Include_Ind` | `Active_Ind` |
|---|---|---|---|---|---|---|
| `CMSUNIV` | `*` | `*` | `HEADER_NAMES` | `Enrollee ID\|Contract ID\|Plan ID` | 1 | 1 |
| `CMSUNIV` | `ODAG3` | `51` | `RECORD_TYPE` | `H,D,T` | 1 | 1 |
| `CMSUNIV` | `ODAG3` | `51` | `TRAILER` | `RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP` | 1 | 1 |
| `CMSUNIV` | `ODAG3` | `20` | `HEADER_NAMES` | | 0 | 1 |

The load checks the trailer against the same `TRAILER` row (and the trailer record type of `RECORD_TYPE`); a
wrong count rejects the file as `FILE_TRAILER_COUNT_MISMATCH`. Without a `TRAILER` row the trailer is not checked.
`CHECK_CONTENT = false` checks names and batches only and does not open the files. `validate-config`
reports a row that is not valid (`FILE_CHECK_CONFIG`) and a row that matches no file (`FILE_CHECK_NO_FILE`).

**Large files.** The load streams the file in one pass into PostgreSQL `COPY`. `CHECK_ON_LOAD = true` runs the
`FILE_CHECK` content checks inside `FILE_LOAD` on the copy the load has already downloaded (a file with a problem is
rejected as `FILE_CONTENT_CHECK_FAILED`, with every problem in its error log). Each step's time limit is
`timeout_minutes` in the Step Functions `step_settings`.

**Control report and error log.** Every `FILE_LOAD` run writes one control report for the files it handled,
`<inbound folder>/ControlReport/ControlReport_<YYYYMMDD_HHMMSS>.txt`, pipe-delimited, one line per file:

| Column | Value |
|---|---|
| `Source_System` | `ComplianceSourceSystem.Src_Nm` of the file's source (MHK, Advocate, WellMed ...) |
| `File_Name` | The file |
| `Status` | `ACCEPTED` (loaded), `REJECTED` (not loaded, moved to `Error/`), `FAILED` (a technical failure; the file stays and is tried again) |
| `Total_Rows_In_File` | Data rows read from the file; empty when the file was rejected before it was read |
| `Total_Rows_Loaded` | Rows loaded to the core table; `0` unless accepted |
| `File_Submitted_Date` | The date part of the timestamp in the file name |
| `File_Processed_Date` | When the load finished, in `BUSINESS_TZ` |
| `Error_Message` | Why the file was not loaded, in plain language for the business (for example "No request exists for this run type and reporting period." or "The file contains data that is not in the expected format (first at line 3)."); empty when accepted. Database and system errors never appear here: they are in the error log, the audit table and the support email |

A run that covers several inbound folders writes one report in each. A run that handled no file writes none.

The detail of a rejection (the check, the line number, the message and the record) is in its own file,
`<inbound folder>/ErrorLog/<file name without extension>_errors.txt` (`X.txt` -> `X_errors.txt`), written by `FILE_LOAD` and by `FILE_CHECK`. The rejected file itself
still moves to `Error/`.

The two folder names are the settings `CONTROL_REPORT_FOLDER` (default `ControlReport`) and `ERROR_LOG_FOLDER`
(default `ErrorLog`); `WRITE_CONTROL_REPORT = false` and `WRITE_ERROR_FILE = false` switch them off.

**Error file and failed files.** For every file that fails, `FILE_CHECK` writes
`<inbound folder>/ErrorLog/<file name without extension>_errors.txt` (`X.txt` -> `X_errors.txt`) (settings `WRITE_ERROR_FILE`, on by default, and
`ERROR_LOG_FOLDER`): pipe-delimited,
one line per problem, with the check, the line number, the message and **the record itself**
(`File_Name|Check_Cd|Line_No|Message|Record`). With `CHECK_MOVE_FAILED = true` the failed file is also moved
to `Error/`, so the files that passed can be loaded. The audit row and the email only carry the number of
problems, the first one and the path of the error file.

`FILE_LOAD` writes the same file when it rejects a file, for example for bytes that are not valid in
`FILE_ENCODING` or a value the database refuses (a character the column cannot hold, a value that is too
long, a bad number or date): one line with the reason, the line number and the record.

**Result.** The step's outcome lists every file with `result` (`PASSED` / `FAILED`), its `findings`, and
what the name resolved to: `config` (project / table / source), `run_type`, `version`, `rpt_start`,
`rpt_end`, `batch` and `req_id`. `notes` holds remarks that are not failures, such as a batch that already
has data and would be replaced by the file. The step ends with exit code 1 when a file failed or a folder
could not be read. Each file also gets one row in the framework's audit table,
`CMS_ComplianceExceptionsAudit`: `FILE_CHECK_FAILED` (emailed by `NOTIFY` to `DEFAULT_NOTIFY_EMAILS`) or
`FILE_CHECK_PASSED`. A run that finds the same result for the same
file again adds no new row.

- **Glue:** `FILE_CHECK` is a step of the workflow like the others (`step_settings`), in no schedule by
  default. A project that wants it puts it in front of `FILE_LOAD` in its schedule's `steps`
  (`["FILE_CHECK", "FILE_LOAD", ...]`): the workflow stops when the check fails, unless
  `step_settings.FILE_CHECK.fail_on_problems` is `false`. One run by hand: `src/run_workflow.sh dev ODR FILE_CHECK`.
  `FILE_LOAD` processes every file first and then fails its step when any file was not loaded, which stops the
  workflow (`step_settings.FILE_LOAD.fail_on_problems`, default `true`).

## File rules run after the load

The rules engine is not part of the load. `FILE_LOAD` validates the file, stages it and loads it to
core; it never calls the rules engine and never waits for it.

`FILE_RULES` is a separate step. It takes every file that is loaded to core and has not had its rules
yet (`ComplianceFileLoad.Load_Stat = PROMOTED`, `Rules_Stat = NOT_RUN`), runs the rules bound to it in
`ComplianceRuleBinding`, and records the outcome:

| Outcome | `Rules_Stat` | What else happens |
|---|---|---|
| All rules pass | `PASSED` (`PASSED_WITH_WARNINGS` under `FILE_RULES_MODE` = `ANNOTATE`) | `FILE_RULES_PASSED` on the batch timeline |
| A rule fails | `FAILED` | `FILE_RULES_FAILED` on the timeline, `RULES_VALIDATION_FAILED` audit event, emailed by `NOTIFY` |
| No binding for the file | `SKIPPED` | nothing |
| The engine could not run | `ERROR` | audited once, the step exits 1, the file is tried again on the next run |

- A rule failure changes nothing else: the data stays in core, the file stays in `Archive`, the batch
  keeps its status and closes as usual. Acting on a failed rule is a decision for the project.
- Only the batch's current load is checked; a load replaced by a newer file is not. A file loaded again
  is checked again.
- The engine is given the staging and the core table of the file (`stg_schema_nm`, `stg_table_nm`,
  `core_schema_nm`, `core_table_nm`) with the batch and load ids.
- **Glue:** `FILE_RULES` is its own workflow execution, on the `file_rules` schedule of each project
  (`stepfunctions/_local.tf`). It is off until the runner has a rules engine: set `gre_entrypoint` (or
  `rule_engine`) in `glue/_local.tf`, then `file_rules_enabled = true`. By hand:
  `src/run_workflow.sh dev ODR FILE_RULES,NOTIFY`.


## Scheduled batches: when they are created and for which dates (metadata)

Scheduled batch creation is configured in the metadata with two SQL statements. Nothing about it is in
the DAG, the Airflow Variable, the Glue job or the code. The scheduler only starts `BATCH_CREATION` for a
project, normally once a day, and a run started by hand behaves the same way.

| Question | Table (one statement per ...) | Column |
|---|---|---|
| **When** are batches created? | `ComplianceRunType` (run type) | `Batch_Schedule_Sql_Txt` |
| **Which report dates** does a batch get? | `ComplianceDataSetSourceXwalk` (project, table, source, run type) | `Rpt_Dt_Sql_Txt` |

Which tables and sources get batches of a run type is the crosswalk itself, as before: one active,
effective row per project, table, source and run type. A table with weekly and monthly batches has a row
under each run type; a source with only monthly batches has no row under the weekly run type.

### When: `ComplianceRunType.Batch_Schedule_Sql_Txt`

A single `SELECT` that returns **one row on a day batches are due and no row on any other day**.

- `{run_date}` is the run date (today in `BUSINESS_TZ`, or `as_of`); `{project_cd}` and `{run_ty}` are
  the project and run type of the run. Use `{run_date}`, not `CURRENT_DATE`, so a re-run for a missed day works.
- What the row contains is up to you, and **every column it returns can be used by name in the report
  date statements** — for example a `period_dt` the dates are counted from.
- A run type without a statement gets no scheduled batches (only a run that passes `period`, below).

Examples (PostgreSQL):

| Batches are due | `Batch_Schedule_Sql_Txt` |
|---|---|
| Every day | `SELECT 1 AS due` |
| Every Monday | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE extract(dow FROM d) = 1` |
| First 5 days of the month | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE extract(day FROM d) <= 5` |
| Only the Sunday before the 3rd Tuesday of the month | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE extract(dow FROM d) = 0 AND extract(day FROM d + 2) BETWEEN 15 AND 21` |
| Any day of the 4th quarter | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE extract(quarter FROM d) = 4` |
| Any day of the second month of the second quarter (May) | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE extract(month FROM d) = 5` |

### Which dates: `ComplianceDataSetSourceXwalk.Rpt_Dt_Sql_Txt`

A single `SELECT` that returns **one row with `rpt_start` and `rpt_end`** for that table, source and run
type. It only runs on a day the run type is due.

- It can use `{run_date}`, `{project_cd}`, `{run_ty}`, `{table_nm}`, `{src_id}` and every column of the
  schedule row (`{period_dt}` in the example below).
- **No row** = this table has no batch this time (the other tables of the run type are not affected).
- A crosswalk row **without** a statement uses `rpt_start` / `rpt_end` of the schedule row when the
  schedule statement returns them (one default for the run type); otherwise the run reports it as an error.

For example "the day before the run": `SELECT CAST({run_date} AS DATE) - 1 AS rpt_start, CAST({run_date} AS DATE) - 1 AS rpt_end`

### How a run uses them

1. For every scheduled run type of the project, the schedule statement runs for the run date. No row:
   every crosswalk row of that run type is reported as `not due`.
2. Otherwise each effective crosswalk row's date statement runs and the batch is created with those dates.
3. **One batch per window** — a stretch of consecutive due days with the same report dates gets one
   batch: the first run inside it creates the batch, later runs find it, and a missed first day is caught
   up on any later day of the stretch. The next stretch gets a new batch, even when its report dates are
   the same as last time. (A stretch longer than 366 days starts a new batch.)
4. A statement that fails, returns more than one row, or returns dates that are not valid is reported for
   that crosswalk row; the other rows are still processed and the step ends with exit code 1.

Both statements must be a single `SELECT` (or `WITH ... SELECT`) and runs in a read-only transaction with the framework's database account. Placeholders are bound as
parameters, so write `CAST({run_date} AS DATE)`; a placeholder the run does not have is an error that
lists the available ones. `validate-config` runs the schedule statements for today (or `as_of`)
and, when a run type is due, every date statement (`BATCH_SCHEDULE_SQL`, `RPT_DT_SQL`).

A run that passes `period` (a built-in period, below) creates one batch for that period and ignores both
statements for that run.

### Example: weekly and monthly submissions around a Tuesday

This reproduces a typical requirement; none of it is in the code. Two run types (any codes; here `MNT`
monthly and `WKL` weekly), both due from the Sunday before a Tuesday through the Thursday after it:
`MNT` in the week of the **3rd Tuesday** of the month, `WKL` in every other Tuesday week. The schedule
row returns that Tuesday as `period_dt`, so every day of a window gives the same dates, also when the
week straddles two months.

`Batch_Schedule_Sql_Txt` of `MNT` (`WKL` is the same statement with `<> 3`):

```sql
SELECT tue AS period_dt
FROM (SELECT CAST({run_date} AS DATE) + (2 - CAST(extract(dow FROM CAST({run_date} AS DATE)) AS INT)) AS tue) x
WHERE extract(dow FROM CAST({run_date} AS DATE)) <= 4
  AND (CAST(extract(day FROM tue) AS INT) - 1) / 7 + 1 = 3
```

`Rpt_Dt_Sql_Txt` of the crosswalk rows:

| Crosswalk rows | Report dates | `Rpt_Dt_Sql_Txt` |
|---|---|---|
| CDAG / ODAG tables, `MNT` | previous calendar month | `SELECT CAST(date_trunc('month', p) - interval '1 month' AS DATE) AS rpt_start, CAST(date_trunc('month', p) AS DATE) - 1 AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| CDAG / ODAG tables, `WKL` | 60 days ending the last Saturday | `SELECT p - 62 AS rpt_start, p - 3 AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| `SNPCC1`, `MNT` and `WKL` | last day of the previous month | `SELECT CAST(date_trunc('month', p) AS DATE) - 1 AS rpt_start, CAST(date_trunc('month', p) AS DATE) - 1 AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| `FA1`, `MNT` | last 14 days of the previous month | `SELECT CAST(date_trunc('month', p) AS DATE) - 1 - 13 AS rpt_start, CAST(date_trunc('month', p) AS DATE) - 1 AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| `FA2`, `MNT` | Jan 1 → Jan 31 | `SELECT make_date(yr, 1, 1) AS rpt_start, make_date(yr, 1, 31) AS rpt_end FROM (SELECT CAST(extract(year FROM CAST({period_dt} AS DATE) - interval '1 month') AS INT) AS yr) d` |
| `FA3`, `MNT` | Nov 1 → Dec 31 of the year before | `SELECT make_date(yr - 1, 11, 1) AS rpt_start, make_date(yr - 1, 12, 31) AS rpt_end FROM (SELECT CAST(extract(year FROM CAST({period_dt} AS DATE) - interval '1 month') AS INT) AS yr) d` |
| `FA4`, `MNT` and `WKL` | Nov 1 of the year before → Jan 31 | `SELECT make_date(yr - 1, 11, 1) AS rpt_start, make_date(yr, 1, 31) AS rpt_end FROM (SELECT CAST(extract(year FROM CAST({period_dt} AS DATE) - interval '1 month') AS INT) AS yr) d` |

`FA1`, `FA2` and `FA3` have no crosswalk row under `WKL`, so they get no weekly batches. The yearly FA
dates move to the next year in February (a January run still uses the previous cycle). A daily run
creates the `MNT` batches on 2026-10-18 (ODAG 2026-09-01 → 2026-09-30, FA1 2026-09-17 → 2026-09-30, ...),
finds them on 2026-10-19 to 2026-10-22, and creates nothing on 2026-10-23 and 2026-10-24.

### Built-in periods for a run that passes `period`

`SAME_DAY`, `PREV_DAY`, `PREV_N_DAYS(n)`, `PREV_WEEK_SAME_DAY(n)`, `PREV_CALENDAR_WEEK`,
`CURRENT_CALENDAR_MONTH`, `PREV_CALENDAR_MONTH`, `ROLLING_1_MONTH`, `PREV_CALENDAR_QUARTER`,
`PREV_CALENDAR_YEAR`, `ANNUAL_WINDOW(MM-DD,MM-DD[,MM-DD])` (the same dates every year; moves to the next
year on the third date, by default the day after the window ends). Example, one batch by hand:
`BATCH_CREATION` with `run_type`, `table` and `period = PREV_CALENDAR_MONTH`.

## Stage and core table layout (`ComplianceTargetColumnConfig`)

The staging and core tables of a file can have any layout. Which of their columns the framework fills,
and with what, is in `ComplianceTargetColumnConfig`; **every other column of the staging table is a column
of the file, in table order.**

| Column | Meaning |
|---|---|
| `Project_Cd` | The project |
| `Table_Nm` | The dataset (`Table_Nm` of the file config); `*` = every table of the project |
| `Column_Nm` | The column in the staging and / or core table |
| `Value_Cd` | What fills it (below) |
| `Value_Txt` | Only for `CONSTANT` (the value) and `SQL` (the statement) |
| `Active_Ind` | 1 = the row is in use; 0 = the row is ignored, as if it were not there |

| `Value_Cd` | Value |
|---|---|
| `RUN_TYPE`, `VERSION`, `SOURCE_ID` | The batch's run type, the compliance version the batch was created with (the version part of `Btch_ID`), the batch's `Src_ID` |
| `FILE_NAME` | The name of the file |
| `LOAD_TIMESTAMP` | When the file was loaded: a timestamp, or text `YYYY-MM-DD HH:MM:SS` (business time zone) in a character column |
| `ROW_SEQUENCE` | A running row number, continuing from the highest value in the staging and core tables |
| `CURRENT_FLAG` | Optional, core only: always 1 (the core table holds only the latest load of a batch) |
| `END_TIMESTAMP` | Optional, core only: always empty |
| `LOAD_ID`, `REQ_ID`, `PROJECT`, `TABLE`, `RPT_START`, `RPT_END` | The framework's load number, the batch's request ID, project, dataset and report dates |
| `LOAD_DATE` | The date of the load (business time zone): a date, or text `YYYY-MM-DD` in a character column |
| `CONSTANT` | The fixed value in `Value_Txt`, on every row (for example `Y`, `N`, `0`, a label) |
| `SQL` | The one value returned by the statement in `Value_Txt`, on every row of the load (below) |

Rules:

- The batch column is always named `Btch_ID` and is in both tables; it needs no row.
- The rows need no order. Where a column sits in the table decides its place: derived columns can be at the
  start, at the end or between file columns, and the file's values fill the remaining columns left to right.
- A row for one table wins over the project's `*` row for the same column.
- **How the file's fields reach the table.** The columns in `ComplianceTargetColumnConfig` (and `Btch_ID`) are
  filled by the framework wherever they are in the table. The file's fields fill the other columns, decided by the
  first of these that applies to the file's source:
  1. a `TARGET_COLUMNS` row in `ComplianceFileCheckConfig`: field n goes to the n-th listed column;
  2. a `HEADER_NAMES` row whose names are all column names: by name;
  3. a `COLUMN_COUNT` row: the first columns in table order;
  4. otherwise every column in table order, from the first one on.

  Columns not filled from the file are loaded as NULL. Example: one table, two layouts. MHK files send all 25
  columns in table order; the other sources send 22 and skip `ExRMNumber`, `Filler2` and `Filler3`:

  | Project_Cd | Table_Nm | Src_ID | Check_Cd | Check_Val_Txt | Include_Ind |
  |---|---|---|---|---|---|
  | CMSUNIV | ODAG3 | `*` | `TARGET_COLUMNS` | `MemberFirstName\|MemberLastName\|...\|WasTheInitOrgDetReqtDeniedForLackOfMediNecessity\|SourceUnique_ID` | 1 |
  | CMSUNIV | ODAG3 | `51` (MHK) | `TARGET_COLUMNS` | | 0 |
- **Columns the file does not have (fillers).** A `HEADER_NAMES` row is the list
  of columns the file has, in file order; the file must have exactly that many fields. When every name in the
  list is a column of the staging table (upper and lower case do not matter), each field goes to the column
  of its name. Otherwise the fields fill the staging columns left to right. Without `HEADER_NAMES`, a
  `COLUMN_COUNT` row gives the number of fields (record type included): they fill the first staging columns in
  table order. Every staging or core column that is neither filled from the file nor in
  `ComplianceTargetColumnConfig` is loaded as NULL, so it must allow NULL. With neither row every such column is
  expected in the file. The load skips the header line; it never counts or compares it (that is `FILE_CHECK`).
- **Core holds one load per batch.** When a file is loaded for a batch that already has rows in the core
  table, those rows are deleted by `Btch_ID` and the new rows are inserted, in one transaction. The core table
  needs no flag or end-date column; rows of other batches are not touched.
- **Staging is cleared before a load by run type**: every staging row of the file's run type is removed,
  then the file is loaded (the staging table needs a `RUN_TYPE` column for this, as in the example below).
  Rows of other run types are not touched. A staging table without a run type column is cleared by batch.
- **`SQL` columns.** `Value_Txt` is a single `SELECT` that returns one row with one column, for example
  `SELECT CURRENT_DATE` or a lookup such as `SELECT Audit_ID FROM <db>.Audit_Run WHERE Btch_ID = {btch_id}`.
  It runs once per load, before the file is staged, with the framework's database account; its value goes
  on every row of that load. It can use `{btch_id}`, `{run_ty}`, `{version}`, `{src_id}`, `{file_name}`,
  `{project_cd}`, `{table_nm}`, `{load_id}`, `{req_id}`, `{rpt_start}`, `{rpt_end}` and `{load_date}`, bound as
  parameters (write `CAST({run_ty} AS VARCHAR(20))` where the database needs a type). A statement that fails,
  or returns no row or more than one, stops that load and leaves the file in place. A `CONSTANT` is always
  taken literally: `CURRENT_DATE` as a constant is the text, not today's date.
- A derived column of the core table must also be in the staging table (it is copied), except
  `CURRENT_FLAG`, `END_TIMESTAMP` and `LOAD_TIMESTAMP`.
- A file with record types (a `RECORD_TYPE` row in `ComplianceFileCheckConfig`) has that first field
  dropped at load: it is not a column of the tables.
- **A project with no rows for a table** uses the framework's own column names: staging `btch_id`,
  `load_id`, `src_file_nm`, `stg_load_dtts`; core `btch_id`, `load_id`, `current_ind`, `load_dtts`, `end_dtts`.

Example: tables that end with `StagingCreateDate, Btch_ID, Universe_Vrsn, Run_Ty, FileName, Src_Data_Key,
Load_Dtts` and start with a row ID:

| `Project_Cd` | `Table_Nm` | `Column_Nm` | `Value_Cd` |
|---|---|---|---|
| `CMSUNIV` | `*` | `StagingCreateDate` | `LOAD_TIMESTAMP` |
| `CMSUNIV` | `*` | `Universe_Vrsn` | `VERSION` |
| `CMSUNIV` | `*` | `Run_Ty` | `RUN_TYPE` |
| `CMSUNIV` | `*` | `FileName` | `FILE_NAME` |
| `CMSUNIV` | `*` | `Src_Data_Key` | `SOURCE_ID` |
| `CMSUNIV` | `*` | `Load_Dtts` | `LOAD_TIMESTAMP` |
| `CMSUNIV` | `ODAG3` | `ODAG_DE_Table3_2022_ID` | `ROW_SEQUENCE` |

`validate-config` reports a row that is not valid (`TARGET_COLUMN`), a table that does not fit its rows
(`TARGET_TABLE`).

## The core table and the delimiter

`ComplianceSourceFileConfig.Delmtr_Cd` holds the delimiter character itself: `|`, `,`, `*`, `;` ... and `\t` for a
tab. It is one character; empty means a comma. Words such as `PIPE` or `COMMA` are not accepted, and
`validate-config` reports them (`DELIMITER`).

`ComplianceSourceFileConfig.Core_Table_Nm` (required) names the core table in `Core_Schema_Nm`, the way
`Stg_Table_Nm` names the staging table. It can differ from `Table_Nm`.

## Upper and lower case

Codes and values that people type are compared without regard to case, everywhere:

- project, table, source, run type and run category codes in every configuration table, in the
  intake requests, in job parameters (`--project`, `--run-type`, `--table`, a Variable's `project`, ...);
- override type and approval status in `ComplianceBatchOverride`, and a `Reuse_Btch_ID` or `--btch-id`;
- file names against their templates (`FILENAME_CASE_SENSITIVE` now defaults to false), the run-type
  token in a file name, and the template placeholders (`{RunTy}` = `{RUNTY}`);
- step and module names, admin commands, period names and the choice settings (`FILE_RULES_MODE`,
  `NOTIFY_BACKEND`, `RULE_ENGINE`, ...).

The framework keeps codes upper case in what it writes (batch rows, `Btch_ID`, audit rows), so
`odr`, `Odr` and `ODR` are one project. `validate-config` reports `CODE_CASE_DUPLICATE` when two rows of
`ComplianceProject`, `ComplianceSourceSystem` or `ComplianceRunType` differ only in case.

The PostgreSQL schema therefore has no foreign keys on project, source or run-type codes (they would
compare case-sensitively); `validate-config` checks those references instead. Unique indexes on
`UPPER(...)` keep `ComplianceProject`, `ComplianceSourceSystem` and `ComplianceRunType` codes unique
whatever their case. Loading a configuration CSV whose code differs only in case from a row already in
the table is refused: update that row in its existing case, or fix the CSV.

The run categories are `SCHEDULED` (batches created on a schedule for a period; was `ROUTINE`) and
`ADHOC` (batches requested through `ComplianceRequestInTake`).

## Environment in database names (`$env`)

The staging and core database (schema) names and the S3 paths of `ComplianceSourceFileConfig` can carry
a `$env` token, so one set of configuration rows works in every environment (the GRE convention):

| Authored | DEV | TEST | QA | PROD / UAT |
|---|---|---|---|---|
| `CMS_$ENV_STG` | `CMS_DEV_STG` | `CMS_TEST_STG` | `CMS_QA_STG` | `CMS_STG` |
| `cms_core_$env_t` | `cms_core_dev_t` | `cms_core_test_t` | `cms_core_qa_t` | `cms_core_t` |
| `s3://inbound-$env/odr/in/` | `s3://inbound-dev/odr/in/` | `s3://inbound-test/odr/in/` | `s3://inbound-qa/odr/in/` | `s3://inbound-/odr/in/` |

- Columns: `Stg_Schema_Nm`, `Core_Schema_Nm`, `S3_Src_File_Path`.
- The token matches in any casing and is replaced in its own casing (`$env` → `dev`, `$ENV` → `DEV`,
  `$Env` → `Dev`). PROD and UAT replace it with nothing; a doubled underscore left in a database name
  collapses (paths are left as they are, so prefer a database-style name where PROD has no suffix).
- Keep the token in the middle of a database name: at the very end or start it leaves the underscore
  behind in PROD and UAT (`CMS_STG_$ENV` → `CMS_STG_`), exactly as GRE does.
- The environment is the `ENVIRONMENT` setting (the Glue jobs get `--FRAMEWORK_ENVIRONMENT` = the deploy `env`, upper-cased); `ENV_VALUE` overrides the replacement text for
  that environment (e.g. `ENV_VALUE=uat` where UAT databases do carry a suffix).

## Running steps by hand

```bash
src/run_workflow.sh dev ODR FILE_LOAD,BATCH_CLOSE                               # now, in this order
src/run_workflow.sh dev UNIVERSE BATCH_CREATION                                 # whatever is due today
src/run_workflow.sh prod ODR BATCH_CREATION --as-of 2026-09-01   # missed run
src/run_workflow.sh dev all_projects NOTIFY
src/run_workflow.sh dev ODR FILE_LOAD --metadata-schema cms_compliance_v2 --metadata-db compliance   # another metadata schema / database
```

Or, in the Step Functions console, **Start execution** on `compliance_batch_framework_<PROJECT>_<env>` with
input like `{"steps": ["FILE_LOAD", "BATCH_CLOSE"], "run_type": "DAILY"}`. The execution history shows,
per project, every run, its input and each step's Glue run.

Commands that are not steps run on the runner job directly:

```bash
R=compliance_batch_framework_runner_dev
aws glue start-job-run --job-name $R --arguments '{"--FW_ARGS": "validate-config"}'
aws glue start-job-run --job-name $R --arguments \
  '{"--FW_ARGS": "close-batch --btch-id 20260201_ODR_ROPENS_50_DAILY_2026_1 --closed-by jdoe"}'
aws glue start-job-run --job-name $R --arguments '{"--FW_ARGS": "health"}'
```

Output goes to CloudWatch Logs (`/aws-glue/python-jobs/output` and `.../error`); the first line of every
run names the module and project.

## Failure alerts

| What | Who is told | How |
|---|---|---|
| A step fails, or the input names an unknown step | the project's `project_alert_emails` + `alert_email` | the workflow publishes: subject `[FAILED] project ODR dev: StepFailed`, body with the schedule (or "manual run"), the failed step (`step BATCH_CLOSE (2 of 3)`), the Glue error and a link to the execution |
| An execution times out (`workflow_timeout_hours`) or is aborted | same | EventBridge rule on the workflow's `TIMED_OUT` / `ABORTED` events |
| A schedule cannot start its workflow (after 1 hour of retries) | `alert_email` | a CloudWatch alarm on the rule's `FailedInvocations` emails |
| A Glue run fails, times out or errors (also runs started by hand: `validate-config`, `metadata_load`) | glue `alert_emails` | EventBridge rule `..._glue_failed_<env>` → SNS `..._glue_alerts_<env>` |
| Business events: quarantined file, missing source, invalid override, technical failure of a file | `DEFAULT_NOTIFY_EMAILS` (the file config holds no recipients or subjects) | the framework's `NOTIFY` step (SES) |

SNS email subscriptions must be confirmed once from the email SNS sends.

## Fallbacks and safeguards

| Situation | What happens |
|---|---|
| An email cannot be sent (SES error, throttling) | The event stays unsent and is retried by the next `NOTIFY`; the step exits 1, so the workflow alerts through SNS |
| Two runs overlap (a manual run during a schedule, a slow 15-minute cycle) | Every step is safe to overlap: a file being loaded is locked (the other run reports it `IN_PROGRESS`), batches are locked while promoted or closed, `NOTIFY` claims each email with `SKIP LOCKED` (no duplicates) |
| Two projects share an inbound folder | A project's `FILE_LOAD` only takes files matching its own templates there; unmatched files are quarantined only in a folder that belongs to one project alone (and left for the all-projects sweep otherwise) |
| One file keeps failing technically | Recorded `FAILED_TECHNICAL`, retried by every sweep, reported once (`FILE_TECHNICAL_FAILURE`), listed by `health`; the rest of the workflow carries on |
| Too many Glue runs at once (`runner_max_concurrent_runs`, Glue throttling) | The step retries with backoff (5 attempts from 1 minute) |
| A Glue run fails | The workflow stops and alerts; the next scheduled run tries again (steps are idempotent) |
| A missed or failed schedule | Re-run by hand with `--as-of <date>` |
| Schedule delivered twice (at-least-once) | Harmless: every step is idempotent |
| Daylight saving | Schedules are UTC, so local run times move by an hour; batch dates use `BUSINESS_TZ` (America/Chicago) |
| Unknown step name | `plan` refuses it in a schedule; the workflow refuses it (and alerts) for a manual run |

## Layout and submodules

```
compliance_batch_framework/
  src/                                        framework source, docs, wheel build (not deployed)
  code/                                       uploaded to S3 by the glue submodule (see *S3 layout*)
    glue_framework_entry.py                   runner entry point (Glue arguments -> `framework` CLI)
    glue_job_metadata_load.py  rds_conn.py    configuration-load job
    wheels/cms_compliance_framework-*.whl     the framework, built from src/ (committed)
  glue/
    env-config/us-east-1/                       common.tfvars (tags) + dev / test / prod.tfvars (secret, data buckets, emails)
    main.tf          code upload, runner and metadata_load jobs
    iam.tf           Glue role
    cloudwatch.tf    failed-run rule, SNS alert topic
    _variable.tf  _local.tf  _data.tf  outputs.tf  backend.tf  terragrunt.hcl
  stepfunctions/
    env-config/us-east-1/                       common.tfvars (tags) + dev / test / prod.tfvars (alert emails, schedules on/off)
    main.tf          one workflow per project (workflow.asl.json.tftpl)
    iam.tf           workflow and EventBridge roles
    schedules.tf     EventBridge schedule rules
    cloudwatch.tf    SNS topics, stopped-execution rules, failed-schedule alarms
    _variable.tf  _local.tf  _data.tf  outputs.tf  backend.tf  terragrunt.hcl
```

`backend.tf` declares the S3 backend (`backend "s3" {}`); the repository root's `remote_state` fills in
bucket, key and lock table. If the root instead *generates* `backend.tf`, delete these two files.

| Submodule | Creates | Needs first |
|---|---|---|
| `glue` | role `compliance_batch_framework_glue_<env>`, jobs `..._runner_<env>` / `..._metadata_load_<env>`, S3 objects, rule `..._glue_failed_<env>`, SNS `..._glue_alerts_<env>` | — |
| `stepfunctions` | roles `..._workflow_<env>` / `..._events_<env>`, workflows `..._<PROJECT>_<env>`, rules `..._<PROJECT>_<env>_<schedule>` + failure alarms, `..._stopped` rules, SNS `..._<PROJECT>_<env>_failures` / `..._ops_<env>` (log groups only with `enable_workflow_logs`) | `glue` |

Existing resources it uses (not managed here): the artifacts bucket, the RDS database and its Secrets
Manager secret (`host, port, username, password, dbname`), the Glue connection(s) to the RDS VPC, the
data buckets, the SES sender.

Helpers in `src/`: `build_wheel.sh` and the GitHub Actions workflow `github-workflow/build-framework-wheel.yaml`
(rebuild `code/wheels/`), `run_workflow.sh` (start a workflow by hand).

## S3 layout

Everything the jobs read from the artifacts bucket sits under one prefix, classified by type
(`glue/_local.tf`):

```
s3://silverton-maa-global-artifactory-<env>/compliance_batch_framework/
  glue/glue_framework_entry.py        runner job script                 <- code/glue_framework_entry.py
  glue/glue_job_metadata_load.py      metadata_load job script          <- code/glue_job_metadata_load.py
  glue/rds_conn.py                    its connection helper             <- code/rds_conn.py
  whl/cms_compliance_framework-*.whl  the framework (--extra-py-files)  <- code/wheels/*.whl
  config_data/*.csv                   configuration CSVs for metadata_load (uploaded by hand)
  tmp/                                Glue --TempDir
```

The `glue` submodule uploads `glue/` and `whl/` on every apply; the Glue role can read the whole prefix
and write only `tmp/`. Inbound files are not here: they live in the data buckets named by
`ComplianceSourceFileConfig` (`data_bucket_names`), with their `Archive/` and `Error/` subfolders.

## Deployer role (gov-compliance-it-deployer)

Everything this module creates is named `compliance*` and falls inside the role's policies:
IAM roles `role/compliance*` (create, inline policy, attach, pass), Glue jobs `job/*compliance*`, state
machines `stateMachine:compliance*`, SNS topics `compliance*`, EventBridge rules `rule/compliance*`,
CloudWatch alarms, and S3 objects in `silverton-maa-global-artifactory-<env>`. `local.name_prefix`
(`compliance_batch_framework`) must keep starting with `compliance`.

Not granted by the role, so not used: EventBridge Scheduler (`scheduler:*`) and SQS (`sqs:*`) — hence
UTC EventBridge rules and alarms instead of time-zone schedules and a dead-letter queue — and the
`logs:*LogDelivery` actions on `*` that Step Functions needs for CloudWatch Logs (`enable_workflow_logs`
stays `false`; execution history and the failure emails do not depend on it).

`src/docs/deployer/gov-compliance-it-deployer.original.yml` is the role template as provided;
`src/docs/deployer/gov-compliance-it-deployer.yml` is the same role with duplicates removed and a
`MaaGOVCOMPLIANCEITScheduler` policy (Scheduler, SQS, Step Functions log delivery on `compliance*`) that
enables time-zone schedules, a dead-letter queue and workflow logs once it is deployed.

## Deploying (GitHub Actions)

Add to `deploy-<env>.yaml`: module option `compliance_frameworks/compliance_batch_framework`; the
submodule options `glue` and `stepfunctions` already exist. Leave the submodule empty to apply both
(`glue` first).

Each submodule's `terragrunt.hcl` includes the repository root (`find_in_parent_folders()`) for remote
state and provider, and adds its own `env-config/${TF_VAR_region}/common.tfvars` and `${TF_VAR_env}.tfvars`
(the workflow sets `TF_VAR_env` and `TF_VAR_region`).
No credentials are passed: the jobs read the database secret at run time.

Before the first deploy, fill in every `REPLACE_WITH_...`: the Glue connection name in `glue/_local.tf`
and the values in `glue/env-config/us-east-1/<env>.tfvars` (database secret, data buckets,
emails) and `stepfunctions/env-config/us-east-1/<env>.tfvars` (alert emails). Everything else
(names, S3 keys, packages, schema, time zone, projects and schedules) is in each submodule's `_local.tf`.
Locally:

```bash
export TF_VAR_env=dev
cd glue && terragrunt plan
```

Packages: Python shell jobs run Python 3.9 (`python_version`; `glue_version` does not apply to them).
The framework wheel is uploaded from `code/wheels/` and attached with `--extra-py-files` (Python shell
cannot install S3 packages through `--additional-python-modules`); `local.pypi_packages` (`glue/_local.tf`) install from PyPI
through `--additional-python-modules`, like the team's other Glue jobs. Database connections require SSL.

### First run in a new environment

1. The framework tables already exist: they are created separately from `src/framework/sql/schema.sql`
   in the `metadata_schema` of the metadata database (the framework never creates or alters tables).
2. Load the configuration tables (below): project, source system, run type, crosswalk, file config,
   rule binding. The project's staging and core tables are created by the project (their framework
   columns: `docs/framework-package.md`, Onboarding).
3. `validate-config` on the runner — must report no errors.
4. Confirm the SNS subscription emails. `enable_schedules = false` keeps the schedules off until you
   are ready.

## Loading the configuration tables (metadata-load job)

Upload the CSVs to `s3://silverton-maa-global-artifactory-<env>/compliance_batch_framework/config_data/`; one run upserts one CSV
into one table (the header is the column list; audit columns are skipped).

```bash
J=compliance_batch_framework_metadata_load_dev
load() {  # table, file, primary key
  aws glue start-job-run --job-name $J --arguments \
    "{\"--TABLE_NAME\": \"$1\", \"--S3_FILE_NAME\": \"$2\", \"--PRIMARY_KEY\": \"$3\"}"
}
load complianceproject            compliance_project.csv              project_cd
load compliancesourcesystem       compliance_source_system.csv        src_id
load complianceruntype            compliance_run_type.csv             run_ty
load compliancedatasetsourcexwalk compliance_dataset_source_xwalk.csv project_cd,table_nm,src_id,run_ty,effective_start_dt_key
```

`ComplianceSourceFileConfig` (identity `cfg_id`: omit it and add `"--MODE": "insert_only"`) and
`ComplianceRuleBinding` (`project_cd,table_nm,src_id,run_ty,gre_rule_group,gre_rule_variant`) load the
same way. Run `validate-config` after every change.

## Updating

- **Framework code**: edit `src/framework/`, rebuild the wheel (the *Build Compliance Framework Wheel*
  workflow, or `src/build_wheel.sh`), deploy `glue`. A changed `sql/schema.sql` is applied to the
  database separately; the framework never alters tables.
- **Schedules / projects**: edit `local.projects` in `stepfunctions/_local.tf`, deploy `stepfunctions`.
- **Job settings**: edit `glue/env-config/us-east-1/<env>.tfvars` (or `glue/_local.tf` for S3 keys and
  packages), deploy `glue`.
- **Tags**: edit `env-config/us-east-1/common.tfvars` in both submodules.

## The framework wheel

`code/wheels/cms_compliance_framework-<version>-py3-none-any.whl` is `src/framework/` packaged for pip; Glue
installs it on every run, so a rebuilt wheel takes effect on the next run after `glue` is deployed. How
to edit, rebuild (GitHub Actions or locally) and inspect it: [`src/README.md`](src/README.md).
