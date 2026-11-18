# Government Regulatory Framework – Tables Used by Each Step

This page lists, for each step (DAG), the tables it reads and writes, what each column holds, the values allowed, and what the step does with it.

## Common rules for all tables

| Item | Rule |
|---|---|
| Codes | Upper case letters, digits and `_`. Not case sensitive. Max length: project 30, table 63, source 30, run type 20, version 10 |
| `*` | Means "all". Allowed in `Table_Nm`, `Src_ID`, `Run_Ty`, `Check_Field_Nm` where stated. A specific row wins over a `*` row |
| `Active_Ind` | `1` = in use, `0` = switched off. Rows are switched off, not deleted |
| Lists | Comma separated, no spaces. Example: `DLY,WKLY,MNT` |
| Dates | `DATE '2026-10-20'`. Empty end date (`NULL`) = no end |
| `$env` | Allowed in database names and NAS paths. Replaced by the environment (DEV, QA, PROD). Not replaced inside SQL text |
| SQL text columns | One statement only. Placeholders like `{rpt_start}` are passed as values. Write `{rpt_start}`, not `'{rpt_start}'` |
| Audit columns | `Load_Dtts`, `LoadUser_ID`, `LastUpdt_Dtts`, `LastUpdt_ID` are filled by the database. Leave them out of inserts |
| Framework tables | `GOVT_RGLTRY_RequestControl`, `GOVT_RGLTRY_FileLoad`, `GOVT_RGLTRY_RequestFileDetail`, `CMS_GOVT_RGLTRY_ExceptionsAudit`, `GOVT_RGLTRY_Lock` are written by the framework only. Do not change them by hand unless a runbook procedure says so |

## Order of steps

| # | Step | What it does |
|---|---|---|
| 1 | BATCH_CREATION | Creates the batches that are due |
| 2 | FILE_CHECK | Checks waiting files. Loads nothing |
| 3 | FILE_LOAD | Loads files from the NAS into staging and core |
| 4 | SOURCE_LOAD | Pulls internal sources with a query into staging and core |
| 5 | OVERRIDE_DECISIONS | Applies approved carry-forwards and reopens |
| 6 | EXPORT_LOAD | Copies batch data from core into the export table |
| 7 | FILE_RULES | Runs the data quality rules (GRE) on each exported batch |
| 8 | BATCH_CLOSE | Closes batches whose close date has come |
| 9 | NOTIFY | Emails the open alerts |

---

## 1. BATCH_CREATION

Creates one row per batch and source in `GOVT_RGLTRY_RequestControl`. Batches come from run type schedules, the batch calendar and ad-hoc requests.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_Project` | Read | Project must be active |
| `GOVT_RGLTRY_Table` | Read | Table group, when a run is limited to a group |
| `GOVT_RGLTRY_SourceSystem` | Read | Source must be active (ad-hoc requests) |
| `GOVT_RGLTRY_RunType` | Read | Schedule, SLA, category |
| `GOVT_RGLTRY_RunTypePeriod` | Read | Report dates of each run type |
| `GOVT_RGLTRY_DataSetSourceXwalk` | Read | Which sources get batches for which run types, version |
| `GOVT_RGLTRY_BatchCalendar` | Read | Extra batches on fixed dates |
| `GOVT_RGLTRY_RequestInTake` | Read / Write | Ad-hoc requests. Writes the last day handled |
| `GOVT_RGLTRY_SourceFileConfig`, `GOVT_RGLTRY_InternalSourceConfig` | Read | An ad-hoc request's source must have a file or query config |
| `GOVT_RGLTRY_RequestControl` | Write | The batch rows |
| `GOVT_RGLTRY_RequestFileDetail` | Write | `BATCH_CREATED` event |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `INTAKE_FAILED`, configuration errors |
| `GOVT_RGLTRY_Lock` | Write | `SEQ:` lock while a batch ID is chosen, `INTAKE:` lock per request |

### GOVT_RGLTRY_Project

One row per project.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | Up to 30 characters | Project code. Part of the batch ID and used in every other table |
| `Project_Desc` | Yes | Text | Description |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = no batches are created for the project |

### GOVT_RGLTRY_Table (optional)

Groups tables so a step can run for a group (for example all ODAG tables).

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Yes | Up to 63 characters | Table code used in all other tables |
| `Table_Grp_Cd` | Yes | Up to 30 characters, e.g. `ODAG` | Group name. Used by the `table_group` run option |
| `Table_Desc` | No | Text | Description |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = the table is left out of its group |

### GOVT_RGLTRY_SourceSystem

One row per source. Shared by all projects.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Src_ID` | Yes | Up to 30 characters, e.g. `51` | Source code. Stored on batch rows, file configs and core rows |
| `Src_Nm` | Yes | Text, e.g. `MedHOK` | Name shown in reports and the control report |
| `Src_Ty` | Yes | Free text, e.g. `FDR`, `INTERNAL` | Information only |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = files of this source are rejected and ad-hoc requests fail |

### GOVT_RGLTRY_RunType

One row per run type. Shared by all projects. Decides when batches are created, when they close, and whether they are scheduled or ad hoc.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Run_Ty` | Yes | Letters and digits, up to 20, e.g. `DLY`, `WKLY`, `MNT`, `CMS` | Run type code. Appears in file names and batch IDs |
| `Run_Ty_Desc` | Yes | Text | Description |
| `Run_Category_Cd` | Yes | `SCHEDULED`, `ADHOC` | `SCHEDULED` = batches from the schedule, exported from the close date. `ADHOC` = batches from requests (and from a schedule if set), stay open until released |
| `SLA_Days` | Yes | Whole number, 1 or more | Close date = request date + `SLA_Days` - 1. `1` = closes the same day |
| `Carry_Fwd_Ind` | Default 0 | `1`, `0` | `1` = REUSE (carry-forward) overrides are allowed |
| `Sched_Cd` | No | `DAILY`, `WEEKLY`, `MONTHLY` | When batches are created. Empty = no schedule code |
| `Sched_Day_Cd` | With `WEEKLY` / `MONTHLY` | `MON`…`SUN`, `1`…`31`, `LAST` | WEEKLY: the weekday. MONTHLY: the day number, `LAST`, or a weekday with `Sched_Week_Cd` |
| `Sched_Week_Cd` | MONTHLY on a weekday | `1`, `2`, `3`, `4`, `LAST` | Which weekday of the month, e.g. 3rd Tuesday |
| `Sched_Months_Txt` | No | e.g. `1,4,7,10` | MONTHLY only in these months (quarterly) |
| `Sched_Window_Days` | Default 1 | 1 or more | A missed day is still created up to this many days later, with the same report dates |
| `Sched_Offset_Days` | Default 0 | -31 to 31 | Moves the scheduled date. `-2` with 3rd Tuesday = the Sunday before |
| `Batch_Schedule_Sql_Txt` | No | One `SELECT` | Used when `Sched_Cd` is empty. Returns a row on a due day, no row otherwise |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = no batches for this run type |

### GOVT_RGLTRY_RunTypePeriod

Report dates of each scheduled run type, set once per project. A table row wins over the `*` row. A period on the crosswalk row wins over both.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Default `*` | A table or `*` | `*` = every table of the project |
| `Run_Ty` | Yes | A run type | |
| `Rpt_Period_Cd` | This or the SQL | Period code, e.g. `PREV_DAY`, `PREV_CALENDAR_WEEK`, `PREV_CALENDAR_MONTH`, `PREV_CALENDAR_QUARTER`, `RANGE(WEEK,5,1)`, `ANNUAL_WINDOW(MM-DD,MM-DD[,MM-DD])` | Report start and end dates, counted from the scheduled date. `ANNUAL_WINDOW` gives the same dates every year, e.g. `12-01,01-31`; the optional third date is when it moves to the next window |
| `Rpt_Dt_Sql_Txt` | This or the code | One `SELECT` returning `rpt_start`, `rpt_end` | For dates no code can express. Use the code or the SQL, not both |
| `Active_Ind` | Default 1 | `1`, `0` | |

Ad-hoc run types need no row. Their dates come from the request.

### GOVT_RGLTRY_DataSetSourceXwalk (crosswalk)

One row per table and source. Lists the run types the source gets batches for, from which date, and the report version.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | Existing codes | This source sends this table |
| `Run_Ty` | Yes | One run type or a list, e.g. `DLY,WKLY,MNT` (up to 200) | Run types the source gets batches for. Only listed run types apply |
| `Effective_Start_Dt_Key` | Yes | Date | First run date the row applies |
| `Effective_End_Dt_Key` | No | Date | Last run date. Empty = no end. End a row and add a new one to change the version |
| `Rpt_Version` | Yes | Up to 10, e.g. `2200` | Report version. Stored on each batch and written to the `VERSION` column |
| `Rpt_Period_Cd` | No | Period code | Only when this source's dates differ from `GOVT_RGLTRY_RunTypePeriod` |
| `Rpt_Dt_Sql_Txt` | No | One `SELECT` | Same as above, for dates no code can express |
| `Include_Ind` | Default 1 | `1`, `0` | On a row for a single run type: `0` stops that run type for the source from the start date |
| `Active_Ind` | Default 1 | `1`, `0` | |

A row for a single run type that is also in the source's list replaces the list entry for that run type while it is effective (different version, dates, or `Include_Ind = 0`).

Report dates are taken from the first that has them: crosswalk `Rpt_Period_Cd`, crosswalk `Rpt_Dt_Sql_Txt`, table row in `GOVT_RGLTRY_RunTypePeriod`, project `*` row.

### GOVT_RGLTRY_BatchCalendar (optional)

Batches on fixed dates with fixed report dates. Used for an extra or moved submission.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Default `*` | A table or `*` | `*` = every table of the run type |
| `Run_Ty` | Yes | Any active run type | |
| `Req_Dt_Key` | Yes | Date | The day the batch is created |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Yes | Dates | Report dates of the batch |
| `Active_Ind` | Default 1 | `1`, `0` | |

### GOVT_RGLTRY_RequestInTake (ad-hoc requests)

One row per request and source. A batch is created on each day of the request window. No crosswalk row is needed.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Intake_ID` | Generated | Number | Request number. Leave out |
| `Project_Cd`, `Table_Nm` | Yes | | |
| `Src_ID` | Yes | A source | One request per source |
| `Run_Ty` | Yes | Any active run type | Usually an ad-hoc one, e.g. `CMS` |
| `Rpt_Version` | Yes | e.g. `2200` | Report version stored on the batch |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Yes | Dates | Report dates of the batch |
| `Req_Start_Dt_Key`, `Req_End_Dt_Key` | Yes | Dates | Days batches are created. Same date twice = one batch |
| `Last_Run_Dt_Key` | Framework | Date | Last day the request was handled. Stops the same day being done twice. Leave out |

If an open batch already exists for the same source, run type and report dates, the request uses it.

### GOVT_RGLTRY_RequestControl (written here, used by all later steps)

One row per batch and source.

| Column | Values | What it is for |
|---|---|---|
| `Req_ID` | Generated number | The batch row number. Used by loads, overrides and the timeline |
| `Project_Cd`, `Table_Nm`, `Src_ID`, `Run_Ty` | Codes | What the batch row is for |
| `Rpt_Version` | e.g. `2200` | From the crosswalk or the request |
| `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key` | Dates | Report period |
| `Req_Dt_Key` | Date | Request date: the day the batch is for. Close date = `Req_Dt_Key + SLA_Days - 1` |
| `Btch_ID` | `YYYYMMDD_PROJECT_TABLE_RUNTYPE`, plus `_2`, `_3` for another report period on the same day | Batch ID. Same for every source of the table |
| `Req_Stat` | `REQUEST_CREATED`, `SOURCE_DATA_READY`, `SOURCE_DATA_READY_NO_DATA`, `SOURCE_DATA_CARRY_FORWARD`, `REQUEST_COMPLETE`, `DATA_NOT_PROVIDED` | Batch status. The last two are closed |
| `Resolution_Ty` | `NEW_FILE`, `NO_DATA`, `CARRY_FORWARD`, `MISSING` | How the batch got (or did not get) its data |
| `Reuse_Btch_ID` | A batch ID | For a carry-forward: the batch whose data is reused |
| `Batch_Close_Ind` | `0`, `1` | `0` = open, `1` = closed |

| `Req_Stat` | Meaning |
|---|---|
| `REQUEST_CREATED` | Created, no data yet |
| `SOURCE_DATA_READY` | Data loaded |
| `SOURCE_DATA_READY_NO_DATA` | Source sent "no data" (empty file, or empty pull on or after the close date) |
| `SOURCE_DATA_CARRY_FORWARD` | Earlier data reused |
| `REQUEST_COMPLETE` | Closed with data |
| `DATA_NOT_PROVIDED` | Closed without data |

---

## 2. FILE_CHECK

Checks the files waiting in the inbound folders. Loads nothing. A failed file can be moved to `Error/` so FILE_LOAD does not pick it up.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_SourceFileConfig` | Read | Folders to look in, file name pattern, delimiter, header and trailer |
| `GOVT_RGLTRY_FileCheckConfig` | Read | Which checks each file gets |
| `GOVT_RGLTRY_RequestControl` | Read | The file must have an open batch |
| `GOVT_RGLTRY_SourceSystem` | Read | The source must exist and be active |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `FILE_CHECK_PASSED` (not emailed), `FILE_CHECK_FAILED` (emailed). One row per file and result |
| `GOVT_RGLTRY_Lock` | Write | `OBJ:` lock while a file is checked |

### GOVT_RGLTRY_SourceFileConfig (file sources)

One row per source that sends files, per table.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Cfg_ID` | Generated | Number | Config number. Stored on each load. Leave out |
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | | |
| `Src_File_Nm_Tmplt` | Yes | Fixed text + tokens (see below) | File name pattern. The name gives the run type and report dates, which find the batch |
| `File_Vrsn_Cd` | With `{VERSION}` | e.g. `22` | Version as written in the file name |
| `Delmtr_Cd` | No | One character: `\|`, `,`, `;`, `*`, `\t` (tab) | Field delimiter. Empty = comma |
| `Src_File_Has_Hdr_Ind` | Yes | `1`, `0` | `1` = first line is a header (not loaded) |
| `Src_File_Has_Trlr_Ind` | Yes | `1`, `0` | `1` = last line is a trailer (not loaded, checked with `TRAILER`) |
| `Allow_Zero_Rcd_Ind` | Yes | `0` | Not used. Put `0`. A file with no rows means "no data" |
| `Src_File_Path` | Yes | `\\server\share\folder`, `//server/share/folder`, `smb://...`, `nas://share/folder` | Inbound NAS folder. `Archive`, `Error`, `ErrorLog`, `ControlReport` are created inside it |
| `Stg_Schema_Nm`, `Stg_Table_Nm` | Yes | Database and table | Staging table |
| `Core_Schema_Nm`, `Core_Table_Nm` | Yes | Database and table | Core table |
| `Active_Ind` | Default 1 | `1`, `0` | `0` = files matching this config are not picked up |

| Token | Meaning |
|---|---|
| `{RUNTY}` | Run type. Required, once |
| `{RPTSTART}`, `{RPTEND}` | Report dates, `YYYYMMDD` unless a format is given. Required, once each |
| `{RPTMONTH}` | Instead of the two dates: a month (`YYYYMM`), first to last day |
| `{TS}` | File timestamp, 12 or 14 digits unless a format is given. Optional |
| `{VERSION}` | Must equal `File_Vrsn_Cd` |
| `{PROJECT}`, `{TABLE}`, `{SRC}` | The row's project, table and source codes |
| `{ANY}` | Any text, ignored |

Formats can be given, for example `{RPTSTART:MMDDYYYY}`, `{RPTEND:YYYY-MM-DD}`, `{TS:YYYYMMDD}`. Two tokens need fixed text between them. The pattern ends with the extension (`.txt`, `.csv`).

Example: `CMSAuth_MedHOK_PYMT_C_22_MNT_20260901_20260930_20261019093000.txt` matches `CMSAuth_MedHOK_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt` with `File_Vrsn_Cd = 22`.

### GOVT_RGLTRY_FileCheckConfig (file checks)

Rows add up: checks set for the project (`*`), the table and the source all apply. For the same check, the most specific row wins: table + source, then table, then source, then `*`.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Default `*` | A table or `*` | |
| `Src_ID` | Default `*` | A source or `*` | |
| `Check_Cd` | Yes | See list below | The check |
| `Check_Field_Nm` | Default `*` | A field name or `*` | The field for field checks. `*` for file-level checks |
| `Check_Val_Txt` | When included | Depends on the check | The check's value |
| `Include_Ind` | Default 1 | `1`, `0` | `0` = switches an inherited check off for the files this row covers |
| `Active_Ind` | Default 1 | `1`, `0` | |

| `Check_Cd` | Field | `Check_Val_Txt` example | What it checks |
|---|---|---|---|
| `RECORD_TYPE` | `*` | `H,D,T` | First field of the header, data rows and trailer |
| `HEADER_NAMES` | `*` | `MemberFirstName\|MemberID\|Amount` | Header names in order (case and spaces ignored). Also decides which staging column each field loads to |
| `COLUMN_COUNT` | `*` | `23` | Number of fields on the header and each data row |
| `TARGET_COLUMNS` | `*` | `MemberFirstName\|MemberID` | Not a check. The staging column of each field, in file order |
| `TRAILER` | `*` | `RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP` | Layout of the trailer. Fields: `RECORD_TYPE`, `FILE_NAME`, `DATA_ROW_COUNT`, `TOTAL_ROW_COUNT`, `TIMESTAMP`, `SKIP`. Counts must match the file |
| `CHARACTER_SET` | `*` | `latin-1` | Only characters of that set, no control characters |
| `INVALID_CHARACTERS` | `*` | `<,>,~` | None of the listed characters in any line (header, data, trailer). An entry of several characters is looked for as that sequence. A comma or the file's delimiter cannot be listed |
| `COLUMN_VALUES` | `*` | `ALL` or `LENGTH,NUMBER,REQUIRED` | Each value fits its staging column: length, number, not empty |
| `MIN_RECORD_LENGTH`, `MAX_RECORD_LENGTH` | `*` | `250` | Characters per data line |
| `MIN_ROWS`, `MAX_ROWS` | `*` | `100` | Number of data rows in the file |
| `MIN_LENGTH`, `MAX_LENGTH` | field | `9` | Characters in the field's value |
| `MIN_VALUE`, `MAX_VALUE` | field | `0` | Field is a number not below / above the value |
| `VALUE_IN` | field | `Y\|N` | Field is one of the listed values |
| `PATTERN` | field | `[A-Z][0-9]{8}` | Whole value matches the regular expression |
| `DATE_FORMAT` | field | `YYYYMMDD`, `MM/DD/YYYY` | Field is a valid date or timestamp in that format |

Always checked, no row needed: file name, batch, source, encoding, blank lines, structure. Field checks skip empty values (use `COLUMN_VALUES` with `REQUIRED` for those).

---

## 3. FILE_LOAD

Loads every waiting file: name → batch → checks → staging → core. Then moves the file to `Archive/` or `Error/`.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_SourceFileConfig` | Read | File layout, folder, staging and core tables |
| `GOVT_RGLTRY_FileCheckConfig` | Read | Checks run at load |
| `GOVT_RGLTRY_TargetColumnConfig` | Read | Columns the framework fills (batch, source, version, load date …) |
| `GOVT_RGLTRY_SourceSystem` | Read | Source active, source name for the control report |
| `GOVT_RGLTRY_RequestControl` | Read / Write | Finds the open batch. Sets status and resolution |
| `GOVT_RGLTRY_BatchOverride` | Read | Approved `LATE_ARRIVAL` / `CORRECTION` lets a file into a closed batch |
| `GOVT_RGLTRY_FileLoad` | Write | One row per file pickup |
| `GOVT_RGLTRY_RequestFileDetail` | Write | `FILE_RECEIVED`, `FILE_LOADED`, `FILE_REPLACED_BEFORE_CLOSE`, `LATE_ARRIVAL_LOADED`, `CORRECTION_LOADED` |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | Rejections and load problems |
| `GOVT_RGLTRY_Lock` | Write | `OBJ:` per file, `REQ:` per batch row, `BTCH:STAGE` per staging table |
| Staging and core tables | Write | The data |

### GOVT_RGLTRY_TargetColumnConfig (derived columns)

Columns of the staging and core tables that the framework fills. Every other staging column is a file field, in table order.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Default `*` | A table or `*` | A table row wins over the `*` row for the same column |
| `Column_Nm` | Yes | A staging / core column | The column to fill |
| `Value_Cd` | Yes | See list below | What fills it |
| `Value_Txt` | For `CONSTANT`, `SQL` | The value, or one `SELECT` returning one value | |
| `Active_Ind` | Default 1 | `1`, `0` | |

| `Value_Cd` | Fills the column with |
|---|---|
| `BATCH_ID` | The batch ID, when the table's batch column is not called `Btch_ID` |
| `SOURCE_ID` | The source code. Required in every staging, core and export table |
| `RUN_TYPE` | The run type |
| `VERSION` | The report version (`Rpt_Version`) |
| `FILE_NAME` | The file name. For pulls: `PROJECT_TABLE_SOURCE_YYYYMMDDHHMISS` |
| `LOAD_TIMESTAMP` | Load date and time. Same value on every row of one load |
| `LOAD_DATE` | Load date |
| `ROW_SEQUENCE` | Running row number, continuing from the highest in staging and core |
| `LOAD_ID`, `REQ_ID` | The load number, the batch row number |
| `PROJECT`, `TABLE` | Project and table codes |
| `RPT_START`, `RPT_END` | Report dates |
| `CURRENT_FLAG` | Core only. Always 1 |
| `END_TIMESTAMP` | Core only. Always empty |
| `CONSTANT` | `Value_Txt` on every row |
| `SQL` | The one value returned by the `SELECT` in `Value_Txt` |

`Btch_ID` needs no row. It is always the batch ID.

Staging can hold every column as text. Core can use real types. Values are converted to the core column's type on the way. A value that does not convert, or an empty value for a NOT NULL column, rejects the whole file (`CORE_TYPE_MISMATCH`) and core is not touched.

### GOVT_RGLTRY_BatchOverride (read here)

FILE_LOAD uses approved `LATE_ARRIVAL` and `CORRECTION` rows to load a file into a closed batch. Full column list under step 5.

### GOVT_RGLTRY_FileLoad (loads and pulls)

One row per file pickup or internal pull.

| Column | Values | What it is for |
|---|---|---|
| `Load_ID` | Generated number | The load number |
| `Cfg_ID` | Number | The file config used (empty for pulls and unmatched files) |
| `Req_ID`, `Btch_ID` | From `GOVT_RGLTRY_RequestControl` | The batch row the load belongs to |
| `File_Hash` | 64 characters | Unique key of the pickup |
| `File_Share` | NAS share, or `QUERY:<connection>` / `QUERY:LOCAL` for pulls | Where the file came from |
| `File_Path` | Folder and file name; for pulls `PROJECT/TABLE/SOURCE/BATCH` | The file |
| `File_Version` | Size and modified time of the file | Tells a new delivery of the same name from the same file |
| `File_Size_Byte` | Number | File size (0 for pulls) |
| `File_Sha256` | 64 characters | Content hash. Same content in another batch raises `FILE_SAME_CONTENT_OTHER_BATCH` |
| `Load_Stat` | See list below | Load status |
| `Rules_Stat` | `NOT_RUN`, `PASSED`, `PASSED_WITH_WARNINGS`, `FAILED`, `SKIPPED`, `ERROR` | Data quality rule result (step 7) |
| `Reject_Rsn_Cd` | An audit event code, e.g. `FILE_REJECTED_NO_BATCH`, `CORE_TYPE_MISMATCH`, `STAGING_TABLE_BUSY` | Why it was rejected or delayed |
| `Stg_Rcd_Cnt` | Number | Rows staged |
| `Error_Txt` | Text | Technical detail of a failure |

| `Load_Stat` | Meaning |
|---|---|
| `RECEIVED`, `STAGING`, `STAGED` | In progress |
| `FILE_LOADED` | In core. The batch's current data |
| `RELOADED` | Was loaded, replaced by a newer load or a carry-forward |
| `REJECTED` | Rejected. File moved to `Error/` |
| `PROCESS_FAILURE` | System problem. Tried again next run |
| `NO_DATA_YET` | Pull had no rows before the close date. Tried again |
| `LOAD_INCOMPLETE` | Staging table busy. Picked up next run |

### GOVT_RGLTRY_RequestFileDetail (batch timeline)

One row per event of a batch row. Written by every step.

| Column | Values | What it is for |
|---|---|---|
| `Detail_ID` | Generated number | |
| `Req_ID`, `Btch_ID` | From `GOVT_RGLTRY_RequestControl` | The batch row |
| `Load_ID`, `Ovrd_ID`, `Intake_ID` | Numbers or empty | The load, override or request involved |
| `Event_Ty` | See list below | What happened |
| `Actor` | `SYSTEM` or a user | Who did it |
| `Event_Txt` | Text | Detail |
| `Event_Dtts` | Timestamp, Central time | When |

| `Event_Ty` | When |
|---|---|
| `BATCH_CREATED` | Batch row created |
| `FILE_RECEIVED` | A file or pull reached the batch |
| `FILE_LOADED` | Data loaded into core |
| `LATE_ARRIVAL_LOADED`, `CORRECTION_LOADED` | Data loaded into a closed batch with an override |
| `FILE_REPLACED_BEFORE_CLOSE` | A new file replaced the batch's data before close |
| `SOURCE_NO_DATA_YET` | Pull had no rows before the close date |
| `CARRY_FORWARD_APPLIED`, `CARRY_FORWARD_REMOVED` | Carry-forward applied or ended |
| `BATCH_RELEASED` | Released early |
| `BATCH_REOPENED` | Reopened |
| `EXPORT_LOADED` | Exported |
| `FILE_RULES_PASSED`, `FILE_RULES_FAILED` | Rules result |
| `BATCH_CLOSED` | Closed |

### CMS_GOVT_RGLTRY_ExceptionsAudit (alerts)

One row per problem or approval. Written by every step. Emailed by NOTIFY.

| Column | Values | What it is for |
|---|---|---|
| `Event_ID` | Generated number | |
| `Event_Ty` | Event code, e.g. `FILE_REJECTED_NO_BATCH`, `SOURCE_MISSING_AT_CLOSE` | What happened |
| `Sevrty` | `ERROR`, `WARNING`, `INFO` | How serious |
| `Project_Cd`, `Table_Nm`, `Src_ID`, `Run_Ty` | Codes | What it is about |
| `Req_ID`, `Load_ID`, `Ovrd_ID`, `Intake_ID`, `Btch_ID` | Numbers / batch ID | The batch, load, override or request involved |
| `File_Ref` | NAS path | The file involved |
| `Actor` | `SYSTEM` or a user | Who did it |
| `Event_Txt` | Text | Detail |
| `Notified_Ind` | `0`, `1` | `0` = not emailed yet. `1` = emailed, or not to be emailed |
| `Event_Dtts` | Timestamp | When |

---

## 4. SOURCE_LOAD

Pulls every open batch whose source has an internal source config, using its query. Rows go to staging, then core.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_InternalSourceConfig` | Read | Query, connection, staging and core tables |
| `GOVT_RGLTRY_TargetColumnConfig` | Read | Derived columns (see step 3) |
| `GOVT_RGLTRY_RequestControl` | Read / Write | Open batches. Sets status and resolution |
| `GOVT_RGLTRY_FileLoad` | Write | One row per pull |
| `GOVT_RGLTRY_RequestFileDetail` | Write | `FILE_RECEIVED`, `FILE_LOADED`, `SOURCE_NO_DATA_YET` |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `SOURCE_QUERY_FAILED`, `SOURCE_DATA_REJECTED`, `CORE_TYPE_MISMATCH` |
| `GOVT_RGLTRY_Lock` | Write | `REQ:` per batch row, `BTCH:STAGE` per staging table |
| Staging and core tables | Write | The data |

### GOVT_RGLTRY_InternalSourceConfig (internal sources)

One row per internal source pulled with a query. Such a source has no file config.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | Yes | | |
| `Src_Db_Ty` | Yes | `TERADATA`, `SQLSERVER` | Where the source data is |
| `Src_Conn_Cd` | No | Airflow Connection id | Empty = the framework's own Teradata. Otherwise the source server's connection |
| `Src_Sql_Txt` | Yes | One `SELECT` | The pull. Column names (`AS`) must match staging columns. Placeholders: `{btch_id}`, `{src_id}`, `{run_ty}`, `{version}`, `{project_cd}`, `{table_nm}`, `{req_id}`, `{load_id}`, `{rpt_start}`, `{rpt_end}`, `{load_date}`, `{file_name}`. `{btch_id}` is shared by all sources, so add `{src_id}` to pull one source |
| `Stg_Schema_Nm`, `Stg_Table_Nm` | Yes | Database and table | Staging table |
| `Core_Schema_Nm`, `Core_Table_Nm` | Yes | Database and table | Core table |
| `Active_Ind` | Default 1 | `1`, `0` | |

| Pull result | `Load_Stat` | Batch status |
|---|---|---|
| Rows | `FILE_LOADED` | `SOURCE_DATA_READY` |
| No rows, before close date | `NO_DATA_YET` | Unchanged. Pulled again next run |
| No rows, on or after close date | `FILE_LOADED` | `SOURCE_DATA_READY_NO_DATA`. Still pulled again until it closes |
| Query failed | `REJECTED` (`SOURCE_QUERY_FAILED`) | Unchanged |
| Columns do not fit | `REJECTED` (`SOURCE_DATA_REJECTED` / `CORE_TYPE_MISMATCH`) | Unchanged |

---

## 5. OVERRIDE_DECISIONS

Applies approved `REUSE` (carry-forward) and `REOPEN` overrides. Reports expired and invalid ones.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_BatchOverride` | Read / Write | Approved overrides. Writes the reused batch ID when it was left empty |
| `GOVT_RGLTRY_RequestControl` | Read / Write | Batch to change. Sets carry-forward or reopens |
| `GOVT_RGLTRY_RunType` | Read | `Carry_Fwd_Ind` must be 1 for `REUSE` |
| `GOVT_RGLTRY_FileLoad` | Write | Earlier load becomes `RELOADED` when data is carried forward |
| `GOVT_RGLTRY_RequestFileDetail` | Write | `CARRY_FORWARD_APPLIED`, `CARRY_FORWARD_REMOVED`, `BATCH_REOPENED` |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `OVERRIDE_APPROVED`, `OVERRIDE_INVALID_DETECTED`, `OVERRIDE_EXPIRED`, `BATCH_REOPENED` |
| `GOVT_RGLTRY_Lock` | Write | `REQ:` per batch row (no wait) |

### GOVT_RGLTRY_BatchOverride (overrides)

One row per exception, for one source of a batch. Applied only when `Apprvl_Stat = 'APPROVED'` and `Valid_Thru_Dt_Key` is today or later.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Ovrd_ID` | Generated | Number | Override number |
| `Override_Ty` | Yes | `REUSE`, `LATE_ARRIVAL`, `CORRECTION`, `REOPEN` | See list below |
| `Req_ID`, `Btch_ID` | Yes | From `GOVT_RGLTRY_RequestControl` | The batch row (one source) |
| `Reuse_Btch_ID` | REUSE only | A batch ID or empty | Batch whose data is reused. Empty = latest earlier closed batch with data |
| `Apprvl_Stat` | Default `PENDING_REVIEW` | `PENDING_REVIEW`, `APPROVED`, `REJECTED` | Only `APPROVED` is used |
| `Valid_Thru_Dt_Key` | Yes to apply | Date | Last day the override can be used |
| `Rsn_Txt` | Recommended | Text | Reason |
| `Reviewed_By`, `Reviewed_Dtts` | When approved | Name, timestamp | Approver |

| `Override_Ty` | Used by | What it does |
|---|---|---|
| `REUSE` | OVERRIDE_DECISIONS | Open batch takes the data of an earlier closed batch. Apply before the close |
| `LATE_ARRIVAL` | FILE_LOAD | Closed batch without data accepts a late file |
| `CORRECTION` | FILE_LOAD | Closed batch with data accepts a corrected file |
| `REOPEN` | OVERRIDE_DECISIONS | Closed batch opens again until new data arrives |

---

## 6. EXPORT_LOAD

Copies each due batch from core into the export table: by plain copy, by a custom SELECT, or by calling a procedure.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_ExportConfig` | Read | Export table, procedure, SQL |
| `GOVT_RGLTRY_RequestControl` | Read | Batches due (open, or changed since the last export) |
| `GOVT_RGLTRY_RequestFileDetail` | Read / Write | Last load and last export of each batch. Writes `EXPORT_LOADED` |
| `GOVT_RGLTRY_RunType` | Read | Category, for held and released run types |
| `GOVT_RGLTRY_SourceFileConfig`, `GOVT_RGLTRY_InternalSourceConfig` | Read | Core table of each source |
| `GOVT_RGLTRY_TargetColumnConfig` | Read | The source column and other derived columns |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `EXPORT_LOAD_FAILED` |
| `GOVT_RGLTRY_Lock` | Write | `REQ:` per batch row, `BTCH:EXPORT` per export table |
| Export tables | Write | The exported rows |

### GOVT_RGLTRY_ExportConfig

One row per project and table.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd`, `Table_Nm` | Yes | | |
| `Exp_Schema_Nm`, `Exp_Table_Nm` | Yes | Database and table | Export table. Needs the core columns, `Btch_ID` and the source column |
| `Exp_Proc_Nm` | No | `database.procedure` | Procedure that does the export. It must delete and insert the rows of the batch and source itself |
| `Exp_Proc_Parm_Txt` | No | e.g. `{btch_id}, {data_btch_id}, {src_id}` | Procedure arguments in order. Placeholders, fixed values in quotes, OUT parameter name. Empty = `{btch_id}, {data_btch_id}` |
| `Exp_Sql_Txt` | No | One `SELECT` | Used when there is no procedure. Its rows are the export rows of the batch and source. Name columns after export columns (`AS`) |
| `Exp_Pre_Sql_Txt` | No | One statement | Runs once per batch, before its sources are exported |
| `Exp_Post_Sql_Txt` | No | One statement | Runs once per batch, after all its sources. Use this for a procedure that works on the whole table |
| `Active_Ind` | Default 1 | `1`, `0` | |

| Mode | When | What writes the rows |
|---|---|---|
| Procedure | `Exp_Proc_Nm` set | The procedure. Called once per batch and source |
| Custom SELECT | `Exp_Sql_Txt` set, no procedure | Framework deletes the source's earlier rows of the period, inserts the SELECT's rows |
| Copy | Neither set | Framework deletes the source's earlier rows of the period, copies from core |

Placeholders: `{btch_id}` (the batch), `{data_btch_id}` (the batch holding the data, which differs for a carry-forward), `{src_id}`, `{run_ty}`, `{project_cd}`, `{table_nm}`, `{version}`, `{req_dt}`, `{rpt_start}`, `{rpt_end}`, `{exp_schema_nm}`, `{exp_table_nm}`.

When is a batch exported:

- Every open batch, on every run.
- A closed batch only when it has new data since its last export (late arrival, correction), or when named in the `batch` option.
- Scheduled run types from the close date. Ad-hoc run types when released, unless held with `hold`.

---

## 7. FILE_RULES

Runs the GRE data quality rules on each batch whose loaded data has not had its rules yet. Started by EXPORT_LOAD. Rules never change the batch status, the export or the close.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_FileLoad` | Read / Write | Loads with `Load_Stat = FILE_LOADED` and `Rules_Stat = NOT_RUN` or `ERROR`. Writes `Rules_Stat` |
| `GOVT_RGLTRY_RequestControl` | Read | Batch details passed to the rules |
| `GOVT_RGLTRY_RuleBinding` | Read | Which GRE rule groups to run |
| `GOVT_RGLTRY_ExportConfig` | Read | A table with an export waits for the batch's export |
| `GOVT_RGLTRY_RequestFileDetail` | Read / Write | Export time of the batch. Writes `FILE_RULES_PASSED` / `FILE_RULES_FAILED` |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `RULES_VALIDATION_FAILED`, `RULES_ENGINE_TECHNICAL_FAILURE` |
| `GOVT_RGLTRY_Lock` | Write | `RULES:<Btch_ID>` |
| GRE tables (`gre_results`, `gre_exceptions` …) | Write (by GRE) | Rule results per batch. `run_key` = `Btch_ID` |

### GOVT_RGLTRY_RuleBinding (optional)

Without a row, the rule group named like the table runs, with the run type as variant. A row names other rule groups. Rows add up.

| Column | Required | Values | What it is for |
|---|---|---|---|
| `Project_Cd` | Yes | A project | |
| `Table_Nm` | Yes | A table or `*` | |
| `Src_ID` | Yes | `*` | Rules run per batch, for all its sources |
| `Run_Ty` | Default `*` | A run type or `*` | |
| `Gre_Rule_Group` | Yes | As defined in GRE | Rule group to run |
| `Gre_Rule_Variant` | Yes | As defined in GRE, or `*` | Variant. `*` = all |
| `Active_Ind` | Default 1 | `1`, `0` | |

What is passed to GRE for each batch:

| Item | Value |
|---|---|
| `run_key` | The batch ID. A rerun replaces the batch's results |
| `extra_filters` | `Btch_ID = <batch>`. Only the batch's rows are checked |
| `text_params` | `btch_id`, `run_ty`, `table_nm`, `project_cd`, report dates, for `{name}` in a rule |

| Result | `Rules_Stat` | Event |
|---|---|---|
| All rules passed | `PASSED` (`PASSED_WITH_WARNINGS` in ANNOTATE mode) | `FILE_RULES_PASSED` |
| A rule failed | `FAILED` | `FILE_RULES_FAILED`, `RULES_VALIDATION_FAILED` (emailed) |
| No active rules, or no data rows | `SKIPPED` | – |
| GRE could not run | `ERROR`, tried again next run | `RULES_ENGINE_TECHNICAL_FAILURE` (emailed) |

To run the rules again for a batch, set its loads' `Rules_Stat` back to `NOT_RUN` and trigger FILE_RULES with the batch's table, run type and source.

---

## 8. BATCH_CLOSE

Closes every open batch whose close date has come, with or without data.

| Table | Read / Write | Used for |
|---|---|---|
| `GOVT_RGLTRY_RequestControl` | Read / Write | Open batches. Sets closed status, resolution, `Batch_Close_Ind = 1` |
| `GOVT_RGLTRY_RunType` | Read | `SLA_Days` (close date), category (ad-hoc stays open until released) |
| `GOVT_RGLTRY_RequestFileDetail` | Read / Write | Release and reopen events. Writes `BATCH_CLOSED`, `BATCH_RELEASED` |
| `GOVT_RGLTRY_BatchOverride` | Read | Reopened batches waiting for data stay open |
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Write | `SOURCE_MISSING_AT_CLOSE`, `BATCH_CLOSE_DEFERRED_LOCKED` |
| `GOVT_RGLTRY_Lock` | Read | A batch being loaded is closed by the next run |

| Batch at close | `Req_Stat` | `Resolution_Ty` | Alert |
|---|---|---|---|
| Data loaded | `REQUEST_COMPLETE` | `NEW_FILE` | – |
| Data carried forward | `REQUEST_COMPLETE` | `CARRY_FORWARD` | – |
| Source sent "no data" | `DATA_NOT_PROVIDED` | `NO_DATA` | – |
| Nothing arrived | `DATA_NOT_PROVIDED` | `MISSING` | `SOURCE_MISSING_AT_CLOSE` (emailed) |
| Being loaded now | Stays open | – | `BATCH_CLOSE_DEFERRED_LOCKED` |

Close date = `Req_Dt_Key + SLA_Days - 1`.

---

## 9. NOTIFY

Emails every alert not sent yet, one email per alert, then marks it sent. A failed email is tried again next run.

| Table | Read / Write | Used for |
|---|---|---|
| `CMS_GOVT_RGLTRY_ExceptionsAudit` | Read / Write | Rows with `Notified_Ind = 0`. Sets `Notified_Ind = 1` after sending |
| `GOVT_RGLTRY_Lock` | Write | `EVT:<Event_ID>` while an alert is sent |

---

## GOVT_RGLTRY_Lock (all steps)

Stops two runs from doing the same work at once. A run removes its locks when it ends. A killed run's lock stays until it expires (default 240 minutes).

| Column | Values | What it is for |
|---|---|---|
| `Lock_Key` | See list below | What is locked |
| `Owner_ID` | Random id | The run holding it |
| `Acquired_Dtts` | Timestamp | When it was taken |
| `Expires_Dtts` | Timestamp | When it can be taken over by another run |

| `Lock_Key` | Held by | What happens when it is taken |
|---|---|---|
| `SEQ:<project>\|<table>\|<run type>` | BATCH_CREATION | Waits up to 300 seconds, then fails and retries next run |
| `REQ:<Req_ID>` | FILE_LOAD, SOURCE_LOAD, EXPORT_LOAD | Waits up to 300 seconds, then fails. BATCH_CLOSE and OVERRIDE_DECISIONS skip to next run |
| `BTCH:STAGE <db>.<table>` | FILE_LOAD, SOURCE_LOAD | File or pull becomes `LOAD_INCOMPLETE`, picked up next run |
| `BTCH:EXPORT <db>.<table>` | EXPORT_LOAD | Waits up to 300 seconds, then `EXPORT_LOAD_FAILED` |
| `RULES:<Btch_ID>` | FILE_RULES | Left for next run |
| `OBJ:<hash>` | FILE_CHECK, FILE_LOAD | File skipped this run |
| `EVT:<Event_ID>`, `INTAKE:<Intake_ID>` | NOTIFY, BATCH_CREATION | Left for the other run |
