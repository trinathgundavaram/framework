# Code review: recommendations and status

Scope: the Airflow / Teradata copy (`airflow/compliance_framework`, every module), the Glue / PostgreSQL copy
(`module/aws/compliance_frameworks/compliance_batch_framework/src/framework`, compared file by file with the
Airflow copy), the Glue entry scripts and the job limits in Terraform. Batch creation was read but is left as
it is, as agreed.

Each item says what it costs today, what to change and whether behaviour changes for users (**BC**).

## Status (version 0.3.27)

| Status | Items |
|---|---|
| Done | A1, A4, A6, A8, B1, B2, B3, B4, C1 to C8 |
| Done in part | A2: filler columns now load as NULL in the Spark engine (not run here: no Spark); the engine is kept |
| Open | A3, A5, A7, A9 (behaviour changes, waiting for a decision) |

How each done item was implemented:

- **A1**: the load takes the field count like the check (`HEADER_NAMES`, else `COLUMN_COUNT`, else the table);
  columns the file does not fill are NULL; the header line is skipped, never counted. `validate-config` flags a
  `COLUMN_COUNT` that disagrees with `HEADER_NAMES`.
- **A4**: errors are recorded as one line with the database's own message (`db.error_text`).
- **A6**: Airflow `execution_timeout_minutes` / `dagrun_timeout_minutes` in the Variable (defaults unchanged).
  Glue already had per-step limits (`timeout_minutes` in the Step Functions `step_settings`).
- **A8**: the load checks the trailer against the `TRAILER` row; `TRAILER_COUNT_CHECK` / `TRAILER_COUNT_REGEX` removed.
- **B1**: `INSERT_BATCH_ROWS` (2000) and `INSERT_BATCH_BYTES` (500000) settings; opt-in `LOAD_ENGINE=FASTLOAD`
  (work table + `INSERT ... SELECT`). Not yet run on Teradata: measure in DEV.
- **B2**: only the derived values that need it are converted before sending.
- **B3**: one reader pass; SHA-256 computed while downloading (NAS) — reading and batching 8.8 s -> 5.6 s per
  million 25-column rows in a local measurement.
- **B4**: opt-in `CHECK_ON_LOAD` (rejects as `FILE_CONTENT_CHECK_FAILED`, every problem in the error log).
- **C1 to C8**: unused helpers removed; one error log writer, header parser and trailer check shared by
  FILE_CHECK and FILE_LOAD; one step list in the Airflow wiring; SES channel removed from the Airflow copy; one
  lock wait loop; the load's resolve step takes only what can fail it; Excel / Parquet readers removed.
Effort: S = under an hour, M = a few hours, L = a day or more, including testing.

## A. Correctness and stability

| # | Finding | Recommendation | BC | Effort |
|---|---|---|---|---|
| A1 | `COLUMN_COUNT` is used by FILE_CHECK but not by FILE_LOAD. A file with fillers and a `COLUMN_COUNT` row but no `HEADER_NAMES` row passes the check (23 fields) and is then rejected by the load, which still expects every free staging column (25). | The load takes its field count the same way as the check: `COLUMN_COUNT`, else `HEADER_NAMES`, else the table. With a count and no names the fields fill the first free columns in table order and the rest are NULL. | No (fixes a mismatch) | S |
| A2 | Glue `LOAD_ENGINE=SPARK` path: a filler column makes it fail (`KeyError`, it asks for a mapping the column does not have). It also rejects record types, `ROW_SEQUENCE` and trailers, and clears staging in a separate commit before the append. | Fix fillers (NULL) if the Spark engine is used; otherwise remove it. The default engine (COPY) streams 1.5 GB into PostgreSQL in about a minute and supports everything. | Only if removed | S (fix) / S (remove) |
| A3 | A load that fails technically (database error, configuration error) is retried on every run, without limit. For a 1.5 GB file that is a full download and parse every hour until someone fixes the cause. | Retry a technical failure up to `MAX_LOAD_RETRIES` times (setting, default 3), then leave it `FAILED_TECHNICAL` until an operator runs `retry-load <Load_ID>` (new admin command). | Yes | M |
| A4 | Teradata driver errors are multi-line stack traces. They are written whole into the step outcome, the task log and the XCom, and their first line (the driver's "Error 528 ... batch request") is what reaches `Error_Txt` and several messages, not the database's own message. | Use the database's message (`db.error_line`) everywhere an error is recorded: sweep errors, technical failures, SQL value and schedule statement errors. Keep the full trace only in the log at DEBUG. | No | S |
| A5 | Locks expire after `LOCK_TTL_MINUTES` (240) and are not refreshed. If a load runs longer than that (several big files, a slow database), another run can take the same batch or staging table. | Refresh the locks a load holds while it streams (every chunk or every few minutes), or refuse to start when the step timeout is longer than the lock TTL. | No | M |
| A6 | Time limits are fixed in code: Airflow FILE_LOAD 2 h, FILE_CHECK 1 h; Glue job 60 min. A run with several 1.5 GB files can hit them. A task killed mid-load leaves a non-terminal load that the next run restarts, so no data is lost, but the run fails. | Read the limit from the Variable (`execution_timeout_minutes`) and from tfvars for Glue, with today's values as defaults. | No | S |
| A7 | With the default `fail_on_problems`, one failed file makes FILE_CHECK fail, so FILE_LOAD is not triggered for the files that passed until the next run. | Default `fail_on_problems` to false for FILE_CHECK when `CHECK_MOVE_FAILED` is on (the failed file is already out of the way and is reported). | Yes | S |
| A8 | Two trailer count checks: FILE_CHECK uses the `TRAILER` row of `ComplianceFileCheckConfig`; FILE_LOAD uses the `TRAILER_COUNT_CHECK` / `TRAILER_COUNT_REGEX` settings. They can disagree. | FILE_LOAD uses the `TRAILER` row too (`DATA_ROW_COUNT` / `TOTAL_ROW_COUNT`); retire the two settings. | Yes (settings removed) | M |
| A9 | NAS "file still arriving" test compares the file's time on the NAS with the Airflow worker's clock. Clock skew can hide a finished file or pick up one still being written. | Compare against the NAS's own time (time of the folder listing), or check that the size is unchanged between two listings. | No | S |

## B. Performance with 1.5 GB files

Not measured yet on Teradata. Measured locally (PostgreSQL stand-in): FILE_CHECK reads 1.5 GB in about 23 s;
the database insert is the expensive part.

| # | Finding | Recommendation | BC | Effort |
|---|---|---|---|---|
| B1 | Teradata staging insert: batched inserts of 2,000 rows or 500 KB, about 3,000 round trips per 1.5 GB file. This is the bulk of the load time. | 1) Make the batch size settings and measure larger batches on your dev system. 2) If still too slow, add `LOAD_ENGINE=FASTLOAD`: FastLoad into a work table per load (`<staging>_W<Load_ID>`), then one `INSERT ... SELECT` into staging. This needs CREATE / DROP TABLE rights in the staging database. Measure first with your largest file. | No (opt-in) | S (1) / L (2) |
| B2 | Every value of every row goes through a conversion function, and the batch size is recomputed per row. About 150 million calls for a 6-million-row file. | A fast path for staging rows (their values are already text, numbers or NULL). | No | S |
| B3 | After the download FILE_LOAD reads the file three times: a SHA-256 pass, a pass to find the last line (trailer), and the parse. | Compute SHA-256 while downloading; find the trailer by holding back one row while parsing (as FILE_CHECK already does). One pass instead of three. | No | M |
| B4 | FILE_CHECK and FILE_LOAD each download the same file from the NAS (2 × 1.5 GB over SMB) and parse it. | Optional `CHECK_ON_LOAD = true`: FILE_LOAD runs the content checks on its own copy before staging, so a separate FILE_CHECK run is not needed for big files. FILE_CHECK stays available as it is. | No (opt-in) | M |
| B5 | Per file, the load re-reads the target column rows, the check rows and both tables' columns from `DBC.ColumnsV`. | Cache them for the duration of one run. | No | S |

## C. Reuse and redundant code

| # | Finding | Recommendation | BC | Effort |
|---|---|---|---|---|
| C1 | Unused after the latest changes: `config.effective_xwalk`, `config.parse_template`; the run type list that every sweep loads but no longer uses; Airflow settings code for `load_engine`, `object_store` and `_basis` names that do not exist in the Airflow copy. | Remove. | No | S |
| C2 | Two writers of the same error log format (`File_Name|Check_Cd|Line_No|Message|Record`), one in FILE_CHECK and one in FILE_LOAD. | One shared function (next to the control report). | No | S |
| C3 | Two parsers of the `HEADER_NAMES` text (the header check and the load's column list). | The header check uses the load's parser. | No | S |
| C4 | The step list and step parameter lists are written out in `compliance_task.py`, `run_framework.py` and `dag_factory.py`. | One definition, imported by the others. | No | S |
| C5 | The Airflow copy carries the SES email channel and `AWS_REGION`; Airflow sends email through its own backend. | Remove from the Airflow copy unless some project sets `NOTIFY_BACKEND=ses` there. | Only if used | S |
| C6 | `db.held` and `db.held_all` repeat the same wait loop. | `held_all` built on `held`. | No | S |
| C7 | The load still carries the "file failed its rules" decision branches from before the rules moved to FILE_RULES; today only an empty file reaches them. | Simplify to the cases that can happen. Touches the batch status logic, so only with a full re-test. | No | M |
| C8 | Excel / Parquet readers exist but are off by default (`SUPPORTED_FILE_TYPES`) and pull in pandas. | Keep only if a project will send such files; otherwise remove. | Only if used | S |

## D. Read, no change recommended

- Batch creation (scheduled and ad hoc): left as is, as agreed. For a long stretch of due days it may run the
  schedule statement once per day of the stretch (up to 366, cached), which is acceptable.
- Core load (delete by `Btch_ID`, then insert), the control report, file name matching, overrides, batch close,
  notifications and the audit trail: no problems found.
- Glue and Airflow copies are in parity: `layout.py`, `filecheck_options.py` and `rules.py` are identical;
  `filecheck.py` and `control_report.py` differ only in file store names. Other differences are the platform
  (PostgreSQL row locks and COPY versus Teradata lock rows and batched inserts).

## Suggested order (as first proposed)

1. A1, A4, C1 to C6 (small, no behaviour change).
2. B2, B3, B5, then measure a 1.5 GB load in dev; B1 based on that measurement.
3. A3, A5, A6, A7, A8, A9 after you confirm the behaviour changes.
4. A2, C5, C7, C8 depending on your answers (Spark engine, SES, Excel / Parquet).
