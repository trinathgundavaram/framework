# Compliance Batch Framework: Airflow + Teradata

A self-contained copy of the compliance batch framework that runs from Airflow DAGs with its metadata,
staging and core tables in **Teradata**. Nothing outside `airflow/` is used or changed. It follows the
layout and connection style of the GRE rules engine's Airflow package (`DQ_COMPLETE_REPO/airflow`) and
the team's DAG conventions (one DAG file, one DAG id, one `<name>_config_var` Variable).

```
airflow/
  osstd_core_cms_compliance_batch_creation.py      one DAG file per step ...
  osstd_core_cms_compliance_file_check.py
  osstd_core_cms_compliance_file_load.py
  osstd_core_cms_compliance_source_load.py
  osstd_core_cms_compliance_export_load.py
  osstd_core_cms_compliance_file_rules.py
  osstd_core_cms_compliance_override_decisions.py
  osstd_core_cms_compliance_batch_close.py
  osstd_core_cms_compliance_notify.py
  osstd_core_cms_compliance_run_module.py          ... one generic DAG that runs any step
  osstd_core_cms_compliance_admin.py               ... and one for validate-config, health, locks
  compliance_framework/
    dags.py        the DAGs (schedule, tasks, triggers, emails, ServiceNow callback), Variable + Connections ->
                   the framework, run_step() / run_command() in-process
    app.py         the application and its steps (modules, step reports)
    batches.py     batch creation (schedule codes, calendar, intake), release / hold, overrides, close
    ingest.py      FILE_LOAD and SOURCE_LOAD
    filecheck.py   FILE_CHECK
    load.py        staging / core layout, staging load, core swap, control report and error log
    export.py      EXPORT_LOAD, FILE_RULES and the GRE bridge
    config.py      metadata rows and validate-config, scope of a run, file check options
    db.py          Teradata layer (transactions, locks), connections (Teradata, NAS, source servers), settings
    audit.py       batch timeline, exceptions audit, notifications
    adapters.py    file stores (NAS, local), email channel, rules engine
    common.py      codes, statuses, dates, clock
    cli.py         command line
    sql/schema.sql                Teradata DDL ({{META_DB}} template)
    sql/approvals.sql             manual override templates
    examples/dev, examples/prod   one example Variable per DAG
    examples/trigger_configurations.json
    requirements.txt
    .airflowignore                keeps Airflow from parsing the package as DAG files
```

## How it fits together

1. **Each step has its own DAG and its own Airflow Variable.** The DAG file only names the two; the
   schedule, connections, scope and settings all come from the Variable.

   | Step | DAG id | Variable |
   |---|---|---|
   | `BATCH_CREATION` | `OSSTD_CORE_CMS_COMPLIANCE_BATCH_CREATION` | `compliance_batch_creation_config_var` |
   | `FILE_CHECK` (optional) | `OSSTD_CORE_CMS_COMPLIANCE_FILE_CHECK` | `compliance_file_check_config_var` |
   | `FILE_LOAD` | `OSSTD_CORE_CMS_COMPLIANCE_FILE_LOAD` | `compliance_file_load_config_var` |
   | `SOURCE_LOAD` (internal sources) | `OSSTD_CORE_CMS_COMPLIANCE_SOURCE_LOAD` | `compliance_source_load_config_var` |
   | `EXPORT_LOAD` (optional) | `OSSTD_CORE_CMS_COMPLIANCE_EXPORT_LOAD` | `compliance_export_load_config_var` |
   | `FILE_RULES` | `OSSTD_CORE_CMS_COMPLIANCE_FILE_RULES` | `compliance_file_rules_config_var` |
   | `OVERRIDE_DECISIONS` | `OSSTD_CORE_CMS_COMPLIANCE_OVERRIDE_DECISIONS` | `compliance_override_decisions_config_var` |
   | `BATCH_CLOSE` | `OSSTD_CORE_CMS_COMPLIANCE_BATCH_CLOSE` | `compliance_batch_close_config_var` |
   | `NOTIFY` | `OSSTD_CORE_CMS_COMPLIANCE_NOTIFY` | `compliance_notify_config_var` |
   | any step (generic) | `OSSTD_CORE_CMS_COMPLIANCE_RUN_MODULE` | `compliance_run_module_config_var` |
   | admin commands | `OSSTD_CORE_CMS_COMPLIANCE_ADMIN` | `compliance_admin_config_var` |

2. **Two Airflow Connections**, both named in the Variables: Teradata (`td_conn_var`) and the NAS file
   server (`wdc_comp_oper_nas_var`, only needed by the DAGs that load files). The bridge exports them as
   `TERADATA_HOST` / `TERADATA_USER` / `TERADATA_PASSWORD` / `TERADATA_LOGMECH` and `NAS_HOST` /
   `NAS_USER` / `NAS_PASSWORD` / `NAS_PORT`.
3. **DAGs start each other** through `trigger_dags` in the Variable (`TriggerDagRunOperator`, not
   waiting): in the examples `FILE_LOAD` and `SOURCE_LOAD` start `EXPORT_LOAD` (which starts `FILE_RULES`),
   `FILE_LOAD` also `OVERRIDE_DECISIONS`, and the others start `NOTIFY`. Without export tables, point
   `FILE_LOAD` / `SOURCE_LOAD` at `FILE_RULES` as before. Each DAG can also have its own `schedule_interval`.
4. A task runs one step in-process. It fails on a framework error (exit code 2), and on "completed
   with problems" (exit code 1) where the step is configured that way; otherwise the outcome is the
   task's XCom.

No `.env` file and no AWS Secrets Manager: credentials come from the Airflow Connections only.

## Deploying

1. Copy the `osstd_core_cms_compliance_*.py` files and the `compliance_framework/` folder into the DAGs
   folder, side by side.
2. Install `compliance_framework/requirements.txt` in the workers' environment. **Airflow 3** (Task SDK
   `airflow.sdk`, the `standard` and `smtp` providers; no Airflow 2 or deprecated imports), on a Python version your
   Airflow 3 release supports. The email tasks and `NOTIFY_BACKEND=airflow` send through the Airflow Connection `smtp_default`.
3. Create the two Connections and one Variable per DAG (below; ready-made in `examples/dev` and
   `examples/prod`, file name = Variable name).
4. The framework tables already exist: they are created separately from `sql/schema.sql` (replace
   `{{META_DB}}` with the metadata database). The framework never creates or alters tables.
5. Load the configuration tables, then trigger `OSSTD_CORE_CMS_COMPLIANCE_ADMIN` with
   `{"command": "validate-config"}`.

To add another DAG for a step (another project, or another cadence), copy that step's DAG file, give
the copy its own `dag_id` and `variable_key`, and create that Variable.

### Airflow Connections

**Teradata** — the Connection named by the Variable's `td_conn_var` (default `compliance_teradata`):

| Field | Value |
|---|---|
| Connection Type | Teradata, or Generic |
| Host / Login / Password | the Teradata system and the framework's service account |
| Extra (optional) | `{"logmech": "LDAP"}` (default LDAP) |

**NAS file server (SMB)** — the Connection named by the Variable's `wdc_comp_oper_nas_var`:

| Field | Value |
|---|---|
| Connection Type | Samba, or Generic |
| Host | the file server, e.g. `nas01.corp.example` |
| Login / Password | the service account that can read, write and delete in the inbound folders (`DOMAIN\user` or `user@domain` where a domain is needed) |
| Port (optional) | default 445 |

The NAS account needs read, write, create-folder and delete on every inbound folder: files are read from
it and then moved into its `Archive` or `Error` subfolder.

The account needs, on the metadata database: SELECT / INSERT / UPDATE / DELETE (no CREATE); on each project's staging and core databases: SELECT / INSERT / UPDATE / DELETE; and SELECT on
`DBC.TablesV` and `DBC.ColumnsV`.

### Airflow Variables (`compliance_<step>_config_var`)

One per DAG. Paste the matching file from [`examples/dev`](examples/dev) or [`examples/prod`](examples/prod)
as the Variable's value (Admin → Variables) and replace the `REPLACE_WITH_...` values. Example,
`compliance_file_load_config_var`:

```json
{
  "td_conn_var": "DEV",
  "wdc_comp_oper_nas_var": "wdc_comp_oper_nas_dev",
  "meta_db": "HSABC_dev",
  "load_env": "DEV",
  "schedule_interval": "*/15 * * * *",
  "email_recipient": "compliance-dev@example.com",
  "project": "CMS_UNIVERSE",
  "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_EXPORT_LOAD", "OSSTD_CORE_CMS_COMPLIANCE_NOTIFY"]
}
```

Keys every Variable can have. Only `meta_db` is required; every other key has a default and is left out when the
default is right (the examples carry only what differs: connections, `load_env`, `project`, schedule, triggers, emails):

| Key | Meaning |
|---|---|
| `meta_db` (required) | Teradata database holding the framework tables |
| `td_conn_var` | Airflow Connection of Teradata. Falls back to `connection_id`, then `gre.connection_id`, then `compliance_teradata` |
| `wdc_comp_oper_nas_var` | Airflow Connection of the NAS file server. Required where files are loaded (`FILE_LOAD`, and the generic and admin DAGs when they touch files) |
| `load_env` | `DEV`, `TEST`, `QA`, `PROD`, ...: shown in the email subjects, and what `$env` in configured database names and paths resolves to (below); falls back to `environment`, then `gre.environment`, else `DEV` |
| `schedule_interval` | Cron expression of the DAG; leave out for a DAG that is only triggered |
| `schedule_timezone` | Optional; schedules run in Central time (`America/Chicago`, with daylight saving) |
| `trigger_dags` | DAG ids to start when the step succeeds; the scope (`project`, `table_group`, `table`, `run_type`) and `as_of` of this run are passed on, so a chain started for one table group stays on it |
| `email_recipient` | Who gets the DAG's success / failure emails (`success_email`, `fail_email` tasks). Without it the DAG has no email tasks |
| `snow_assignment_group` | ServiceNow assignment group of the incident opened when a task fails (default `D&AE - EDE Govt Compliance`) |
| `owner`, `tags` | DAG owner (default `oss`) and tags |
| `settings` | Framework settings by name, only where they differ from the default. Common: `LOCK_TIMEOUT_SECONDS` (300), `LOCK_TTL_MINUTES` (240); plus the step's own, below (defaults in brackets) |
| `fail_on_problems` | `true` / `false`: whether "completed with problems" (exit code 1) fails the task. Default: true, except `OVERRIDE_DECISIONS`. `FILE_LOAD` processes every file first and then fails the task when any file was not loaded (rejected, or a technical failure); the DAGs it triggers are then not started |
| `runs` | Optional list of scopes: one task per entry, each with its own `project` / `run_type` / `period` / ... (and `settings`, `gre`). Without it the DAG has one task, scoped by the Variable's own keys |
| `log_level` | `INFO` (default), `DEBUG`, ... |
| `execution_timeout_minutes` | Time limit of the step's task. Default: `BATCH_CREATION` and `FILE_CHECK` 60, `FILE_LOAD` and `FILE_RULES` 120, the others 30 |
| `dagrun_timeout_minutes` | Time limit of a whole DAG run (default 480) |

Keys by step:

| Step | Scope keys | Settings worth setting |
|---|---|---|
| `BATCH_CREATION` | `project` (required). When batches are due and their report dates come from the metadata (schedule codes, `Rpt_Period_Cd`, `ComplianceBatchCalendar`; below). Optional: `table_group`, `run_type`, `table`, `period`, `lookback_days`, `lookback_weeks` | — |
| `FILE_CHECK` | the scope (`project`, `table_group` or `table`, `run_type`; see "Scope of a run"), the NAS connection as for `FILE_LOAD` | `CHECK_CONTENT` and the file settings of `FILE_LOAD`; which checks a file gets is in `ComplianceFileCheckConfig` |
| `FILE_LOAD` | the scope (`project`, `table_group` or `table`, `run_type`) | `FILE_STORE` (`nas`), `ARCHIVE_FOLDER` (`Archive`), `ERROR_FOLDER` (`Error`), `CONTROL_REPORT_FOLDER` (`ControlReport`), `ERROR_LOG_FOLDER` (`ErrorLog`), `NAS_MIN_AGE_SECONDS` (120), `ARCHIVE_BY_YEAR` (`false`); for large files `INSERT_BATCH_ROWS` (2000), `INSERT_BATCH_BYTES` (500000), `LOAD_ENGINE` (`BATCH`, or `FASTLOAD`), `CHECK_ON_LOAD` (`false`) — see "Large files" |
| `FILE_RULES` | the scope; `gre` (the GRE's own Airflow Variable; every key optional: `meta_db` (default the Variable's `meta_db`), `environment` (`load_env`), `project_name` (`project`), `log_level` (the Variable's, `INFO`), `max_parallel_rules` (1), `connection_type` / `meta_connection` (`teradata`), `run_params` / `text_params` / `extra_filters` (none); plus `package_dir`, the folder holding the GRE's `run_rules.py`, default `<dags>/rules_engine`) | `RULE_ENGINE` (`gre` / `none`), `GRE_ENTRYPOINT`, `FILE_RULES_MODE` (`GATE`) |
| `OVERRIDE_DECISIONS` | the scope | — |
| `BATCH_CLOSE` | the scope | — |
| `NOTIFY` | the scope (no `project`: every pending event, including those of no project) | `NOTIFY_BACKEND` (`airflow` = email through the Airflow SMTP connection `smtp_default`, `log` = write to the task log, the default), `NOTIFY_FROM_EMAIL`, `DEFAULT_NOTIFY_EMAILS` |

A trigger's configuration overrides the Variable for that run: any scope key above, `as_of` (an ISO
date or timestamp used as "now", for a missed day), `settings` and `gre`.

**One DAG, several times (timed runs).** When entries of `runs` have their own `schedule_interval` (and a `name`),
the DAG runs at every one of those times (and at its own `schedule_interval`, if set), with one task. Each run
works with the entry scheduled at its time, e.g. EXPORT_LOAD for MNT at 06:00 and for DAILY at 20:00:

```json
{"meta_db": "HSABC_DEV", "td_conn_var": "DEV", "load_env": "DEV", "project": "CMS_UNIVERSE", "schedule_interval": null,
 "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_FILE_RULES"],
 "runs": [{"name": "DAILY", "run_type": "DAILY", "schedule_interval": "0 20 * * *", "settings": {"RELEASE_TIME": "20:00"}},
          {"name": "MNT", "run_type": "MNT", "schedule_interval": "0 6 * * *"}]}
```

| Run | Works with |
|---|---|
| Scheduled | The entry (or entries, one after the other) whose `schedule_interval` is the run's time |
| Manual with `"run": "MNT"` (or `"DAILY,MNT"`) | Those entries, plus what the trigger gives (`batch`, `release`, `settings`, ...) |
| Manual without `run`, or started by another DAG | The Variable's own scope, plus what the trigger gives |

A DAG never runs twice at the same time (`max_active_runs` 1): a run that is due, or triggered, while another is
running waits and starts after it. Different DAGs can run at the same time; the framework's batch and table locks
keep them off the same batch, staging or export table (the later one waits up to `LOCK_TIMEOUT_SECONDS`). `runs`
entries without `schedule_interval` work as before: one task each, all in every run.

## DAGs

- **Step DAGs** — one task named after the step (or one per `runs` entry), then a `trigger_<dag id>`
  task per `trigger_dags` entry, then `success_email` / `fail_email`. `max_active_runs` is 1 and tasks
  do not retry (`retries: 0`); every step is idempotent, so the next run picks up. DAGs that overlap are
  safe (locks, below).
- **`OSSTD_CORE_CMS_COMPLIANCE_RUN_MODULE`** — the generic DAG. Its `module` parameter names the step
  and the other parameters are that step's (`project`, `run_type`, `period`, `table`, `lookback_days`,
  `lookback_weeks`, `as_of`). A parameter the module does not take is refused. Its
  Variable holds the connections, `meta_db` and the settings of every step; nothing about a step is
  fixed in it. Examples:
  - `{"module": "BATCH_CREATION", "project": "CMS_UNIVERSE", "run_type": "MNT", "period": "PREV_CALENDAR_MONTH"}`
  - `{"module": "FILE_LOAD", "project": "CMS_UNIVERSE"}`
  - `{"module": "FILE_RULES"}`, `{"module": "BATCH_CLOSE", "project": "CMS_UNIVERSE"}`, `{"module": "NOTIFY"}`
- **`OSSTD_CORE_CMS_COMPLIANCE_ADMIN`** — `validate-config`, `health`, `test-connection`, `show-config`,
  `locks`, `release-lock`, `close-batch`:
  `{"command": "close-batch", "args": ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]}` closes every open source
  of the batch; add `"--src-id", "<Src_ID>"` to close one source.
- A missing or invalid Variable never breaks parsing: the DAG is still there, unscheduled, and its task
  fails with the reason when run.
- More trigger examples: [`examples/trigger_configurations.json`](examples/trigger_configurations.json).

## Internal sources: table to table (`SOURCE_LOAD`)

Sources that are tables (on the framework's own Teradata system, on another Teradata system or on a SQL Server)
are pulled by batch with their own `SELECT`, instead of being delivered as files. Everything after the pull is the
same as for a file: the same staging and core tables, column mapping (`ComplianceTargetColumnConfig`,
`TARGET_COLUMNS`), batch status, rules, close, audit, emails and step report.

**One row per source in `ComplianceInternalSourceConfig`** (DDL in `sql/schema.sql`; optional, only for internal
sources). An installation that has the table under its earlier name renames it once:
`RENAME TABLE <META_DB>.ComplianceSourceQueryConfig TO <META_DB>.ComplianceInternalSourceConfig;`

| Column | Meaning |
|---|---|
| `Project_Cd`, `Table_Nm`, `Src_ID` | The source, as in the crosswalk (the source needs no file config) |
| `Src_Db_Ty` | `TERADATA` or `SQLSERVER` |
| `Src_Conn_Cd` | The Airflow Connection of the source's server. Empty for a source on the framework's own Teradata system: its rows are then copied with one `INSERT ... SELECT` and never pass through Airflow (the framework's account needs SELECT on the source tables). `$env` is replaced as in database names |
| `Src_Sql_Txt` | One `SELECT` in the source's own dialect. It can use `{btch_id}`, `{rpt_start}`, `{rpt_end}`, `{run_ty}`, `{version}`, `{src_id}`, `{req_id}`, `{project_cd}`, `{table_nm}`, `{load_id}`, `{load_date}`, bound as parameters. Its column names fill the staging columns of the same name (else in table order, or by `TARGET_COLUMNS`) |
| `Stg_Schema_Nm`, `Stg_Table_Nm`, `Core_Schema_Nm`, `Core_Table_Nm` | As in the file config |

**Connections.** One Airflow Connection per server (or per login); many sources can share one. SQL Server:
host (or `server\instance`), port, schema = default database, login and password, and Extra such as
`{"driver": "ODBC Driver 18 for SQL Server", "encrypt": "yes", "trust_server_certificate": "yes"}`
(`"authentication"` for Azure AD logins, `"login_timeout"` in seconds). Teradata: host, login, password, Extra
`{"logmech": "LDAP"}`. Credentials stay in Airflow; the log shows only the Connection id, host and database. SQL
Server needs `pyodbc` and the Microsoft ODBC driver on the workers. Check a connection with the admin DAG:
`{"command": "test-connection", "args": ["--source", "<Src_Conn_Cd>", "--db-type", "SQLSERVER"]}`.

**Which batches are pulled.** With the scope (`project`, `table_group` or `table`, `run_type`): every open batch
of a query source that has no data yet (`REQUEST_CREATED`, request date reached). With `batch` (one `Btch_ID`, or
several separated by commas): the query sources of those batches, whatever their data; the new pull replaces the batch's rows
(a closed batch still needs its override). A scheduled run type's batch is pulled from its request date on, so data that
is ready early is taken at once. A query that returns no rows **before the batch's due date** (`Req_Dt_Key + SLA_Days - 1`)
means "no data yet": the batch stays `REQUEST_CREATED` (`SOURCE_NO_DATA_YET` in its timeline, `WAITING` in the step
report, the task does not fail) and the next SOURCE_LOAD runs try again; on or after the due date no rows means the
source had no data (`SOURCE_DATA_READY_NO_DATA`). Data pulled early is not pulled again by itself: a later change in
the source needs `batch` (it replaces the batch's rows). Each pull is a row of `ComplianceFileLoad` (`File_Share` = `QUERY:<Src_Conn_Cd>`
or `QUERY:LOCAL`, `File_Path` = project/table/source/batch), so FILE_RULES, the step report and the history work
as for files; no control report file is written for pulls. A query that fails or a value that does not fit
rejects that pull (`SOURCE_QUERY_FAILED` / `SOURCE_DATA_REJECTED`, emailed); the other batches are still pulled
and the task fails at the end. Sources on another server are read in batches of `INSERT_BATCH_ROWS`.

## Export tables (`EXPORT_LOAD`)

Optional step between the load (FILE_LOAD / SOURCE_LOAD) and FILE_RULES: it copies each batch's core rows into
the table's export table, so the rules (and downstream feeds) read the export. Without `ComplianceExportConfig`
rows (or without the table) the step finds nothing to do.

**One row per core table in `ComplianceExportConfig`** (DDL in `sql/schema.sql`): `Project_Cd`, `Table_Nm`,
`Exp_Schema_Nm` (`$env` replaced as in database names), `Exp_Table_Nm`, `Active_Ind`. The export table has the
core table's columns (it may have fewer, and its own identity column); `Btch_ID` is required. Columns of the export
that are not in core are left NULL (`validate-config` warns). The core row ID column is copied as it is, so an
export row can be traced back to core.

**Batch and source in the export.** `Btch_ID` is the table-level batch (every source of the table and run type), so
the export table needs the column mapped to `SOURCE_ID` too: a batch's export rows are replaced per source. The
optional column `Exp_Btch_ID` (another name: `Exp_Btch_Col_Nm` in `ComplianceExportConfig`) is filled with the same
`Btch_ID`; the rules receive it as `exp_btch_id`. A carried-forward copy gets the `Btch_ID` of the batch it is
copied to. An export procedure gets `{src_id}`: it deletes and inserts only that source's rows and fills the source
column.

**Which batches are exported.** With the scope: every batch whose data changed after its last export (a load,
late arrival, correction, carry-forward applied or removed, or a change of the batch it reuses), for every run
type. With `batch`: exactly those batches, again.

**What the export holds.** Per source, run type and report dates, the latest batch with data: exporting it removes
the rows of the other batches of that period and inserts its own. A carried-forward batch gets a copy of the
reused batch's core rows under its own `Btch_ID`, with its own run type, version, request and report dates (the
columns mapped to them in `ComplianceTargetColumnConfig`); every other column, row ID, file name and load times
included, is copied as it is. The reused batch keeps its own rows, so both batches have their data. A batch with
no data (empty file) removes the period's rows. An older batch of a period that has a later batch with data is
skipped (`EXPORT_LOADED` in the timeline says why).

**When: by run type category.** Files always load to core as they arrive. Pulling internal sources (SOURCE_LOAD),
the export and therefore the rules follow the batch's run type (`ComplianceRunType.Run_Category_Cd`). Internal
sources of a scheduled run type are pulled from the request date on (an empty result waits until the due date, see
SOURCE_LOAD); the export:

| Run category | Data exported (and, for ad hoc, internal sources pulled) |
|---|---|
| `SCHEDULED` (DAILY, WEEKLY, MONTHLY, ...) | from the batch's due date on: its close date, `Req_Dt_Key + SLA_Days - 1` (WEEKLY created Sunday 10/18 with `SLA_Days = 6`: Friday 10/23; DAILY with `SLA_Days = 1`: the same day). After that every change is exported at once |
| `ADHOC` | only when released |

`RELEASE_TIME` (setting, `HH:MM` Central time) makes the due date start at that time instead of midnight, e.g.
`"RELEASE_TIME": "20:00"` in the EXPORT_LOAD Variable: a DAILY batch created in the morning is exported from 20:00
that evening. It applies to the steps whose Variable has it (the examples: EXPORT_LOAD only, so internal sources are
pulled in the morning and exported in the evening).

A held batch is listed under `held` in the step report (with the date it waits for); it is not a problem and does
not fail the task. FILE_RULES leaves the loads of tables with an export table until their batch has been exported
(`waiting_for_export` in its report), so the rules always read the export.

**Releasing early** (e.g. weekly data ready on Wednesday): trigger the DAG with `release` = `true` and the scope,
for example `{"project": "CMS_UNIVERSE", "run_type": "WEEKLY", "release": "true"}` (or a `table_group`, a
`table`, or `batch`):

- `SOURCE_LOAD` releases and pulls the scope's held batches of internal sources (and triggers EXPORT_LOAD);
- `EXPORT_LOAD` releases and exports the scope's held batches that have data (batches still without data stay
  held).

Each released batch gets `BATCH_RELEASED` in its timeline. From then on it is treated as due: later changes are
exported at once, and BATCH_CLOSE closes it on its next run (or `close-batch`) without waiting for the SLA, once it
has data. Batches without data still close on their SLA date. Other run types and batches are not touched.

**Holding a run type for business reasons** (scheduled or ad hoc, also past the due date): put
`"hold": "WEEKLY"` (run types, comma separated, or a list) in the Variable of `SOURCE_LOAD`, `EXPORT_LOAD` and
`BATCH_CLOSE`, or give it in a trigger. As long as a DAG gets that input, its project's batches of those run types
are not pulled, exported or closed (listed under `held` / `on_hold` in the report), next week's batches included.
To run them anyway, trigger with `release` = `true` and the scope: the batches it releases go through every step
(pull, export, rules, close), hold or not. Remove the `hold` input to return to the run type's normal release.

Give EXPORT_LOAD its own daily `schedule_interval` at or after `RELEASE_TIME` (the examples: 20:00) so scheduled
batches are exported on their due date even when no file arrives that day, and schedule BATCH_CLOSE after it (the
examples: 22:00), so a batch is exported before it closes.

**Export through a procedure.** When `ComplianceExportConfig.Exp_Proc_Nm` names a procedure (`database.procedure`,
`$env` replaced), EXPORT_LOAD calls it instead of its own delete and `INSERT ... SELECT`: the framework still decides
which batches to export (changes, hold, release, latest batch of the period), takes the locks, runs the call in one
transaction, counts the batch's rows in the export table afterwards and records `EXPORT_LOADED`; the procedure deletes
and inserts the export rows itself. `Exp_Proc_Parm_Txt` lists its parameters in order, e.g.
`{btch_id}, {data_btch_id}, {exp_btch_id}` (the default when empty). Available: `{btch_id}` (the batch exported),
`{data_btch_id}` (the batch whose core rows are its data: itself, the batch it reuses, or NULL when it has no data),
`{exp_btch_id}`, `{reused_btch_ids}` (the batches it reuses, comma separated, whose rows must stay), `{project_cd}`,
`{table_nm}`, `{src_id}`, `{run_ty}`, `{version}`, `{req_id}`, `{req_dt}`, `{rpt_start}`, `{rpt_end}`. Existing
tables: `ALTER TABLE <META_DB>.ComplianceExportConfig ADD Exp_Proc_Nm VARCHAR(256), ADD Exp_Proc_Parm_Txt VARCHAR(1000);`.

Each batch is exported in one transaction (delete, then `INSERT ... SELECT` on Teradata; no row passes through
Airflow) and recorded as `EXPORT_LOADED` in `ComplianceRequestFileDetail`. A batch that fails is rolled back and
reported (`EXPORT_LOAD_FAILED`, emailed); the others are still exported and the task fails at the end, so
FILE_RULES is not triggered on a partial export.

## Time zone

Everything runs in Central time (`America/Chicago`, with daylight saving): business dates, schedules, due dates and
`RELEASE_TIME`. Every timestamp the framework writes (`Created_Dtts`, `Updated_Dtts`, `Event_Dtts`, locks, load
times) is the current Central time with its offset (e.g. `2026-10-14 22:59:02-05:00`), not UTC. No Variable needs
`BUSINESS_TZ` or `schedule_timezone`.

## Scope of a run: project, table group, table, run type

Every step takes the same scope parameters: `project`, then optionally `table_group` **or** `table`, and
optionally `run_type`. The levels:

| Given | The step works on |
|---|---|
| nothing | every project (`BATCH_CREATION` needs `project`) |
| `project` | the whole project |
| `project` + `table_group` | the project's tables in that group (`ComplianceTable`) |
| `project` + `table` | that table |
| any of the above + `run_type` | only that run type |

- `table_group` and `table` need `project`; give one of them, not both.
- Table groups are optional: `ComplianceTable` (`Project_Cd`, `Table_Nm`, `Table_Grp_Cd`, `Table_Desc`,
  `Active_Ind`) is read only when a run names a `table_group`. A project without rows there works exactly as
  before, per project or per table. A group with no active table does nothing (the step reports it).
- What each step does with the scope: `BATCH_CREATION` creates the scope's batches (scheduled and ad hoc requests);
  `FILE_CHECK` / `FILE_LOAD` take only the scope's files from the inbound folders and leave every other file in place
  (a file of another table, or of another run type); `FILE_RULES`, `OVERRIDE_DECISIONS` and `BATCH_CLOSE` act on the
  scope's batches; `NOTIFY` sends the scope's events.
- In a run narrowed to a table, a table group or a run type, a file whose name fits none of the scope's templates is
  left in place (a run for the whole project rejects it).

## What the log shows for a step

At the end of every step the task log has one report, indented (JSON), instead of a single long line:

- `FILE_LOAD`: `result` (`ALL FILES LOADED`, `n FILE(S) NOT LOADED`, `NO FILES`), the folders, the counts
  (`files_found`, `accepted`, `rejected`, `failed`, `skipped`), the control reports written, then one block per
  file: `file`, `status` (`ACCEPTED` / `REJECTED` / `FAILED`, as in the control report), `source`, `load_id`,
  `batch`, `batch_status`, `rows_in_file`, `rows_loaded`, `file_date`, `processed`, and for a file that was not
  loaded `reason` (plain language), `detail` (technical) and `error_log`.
- `FILE_CHECK`: `result` (`ALL FILES PASSED`, `n FILE(S) WITH PROBLEMS`, `NO FILES`), the counts (`files_checked`,
  `passed`, `failed`, `not_checked`), then one block per file: `file`, `result`, `source`, `run_type`, `version`,
  `report_dates`, `batch`, `lines`, `data_rows`, `rows_validated`, `value_rules`, `problems`, `notes`, `error_log`,
  `moved_to`.
- The other steps: `step`, `result` and their details.

A failed step logs its report as an error and the task fails with a one-line message pointing to it. While a
step runs, each event is one line: `<event> | <batch, or project/table/source> | load <Load_ID> | <text>`, and
the progress is logged without any data from the files: the files found in each folder (names), and per file
the bytes read, lines and data rows counted, rows validated, rows staged, rows loaded into core (and earlier rows
of the batch removed), the batch status afterwards, and where the file was moved.
The SMB library's connection messages are no longer written (warnings and errors still are).

## Batch ID (`Btch_ID`)

`Btch_ID` = `{Req_Dt_Key:YYYYMMDD}_{Project}_{Table}_{Run_Ty}`, e.g. `20261019_CMSUNIV_ODAG3_DAILY`: one batch per table,
run type and request date, shared by every source. Each source has its own row in `ComplianceRequestControl`
(`Btch_ID` + `Src_ID` is unique; `Req_ID` identifies the row) with its own status, data, SLA, close, overrides and
timeline. A second report period of the same table and run type on the same request date (an ad-hoc request, an
intake row for another month) gets `_2`, then `_3`: `20261019_CMSUNIV_ODAG3_CMS_2`. The compliance version is in
`Cmplnc_Vrsn` of the batch row.

What that means in use:

- Core, staging and export rows of a batch are told apart by the column mapped to `SOURCE_ID` (required).
- `batch` in SOURCE_LOAD / EXPORT_LOAD and `close-batch` act on every source of the batch (`--src-id` for one).
- Queries and rules that select by `{btch_id}` get every source: add `{src_id}` where they work per source.
- Overrides are entered for one source: `... FROM ComplianceRequestControl WHERE Btch_ID = '...' AND Src_ID = '...'`.

Upgrading an installation from 0.3.37 (DEV: the earlier batches are removed and created again):

```sql
ALTER TABLE <META_DB>.ComplianceRequestControl ADD Cmplnc_Vrsn VARCHAR(10);
DROP INDEX uq_reqcontrol_btch ON <META_DB>.ComplianceRequestControl;
CREATE UNIQUE INDEX uq_reqcontrol_btch (Btch_ID, Src_ID) ON <META_DB>.ComplianceRequestControl;
CREATE INDEX ix_fileload_req (Req_ID) ON <META_DB>.ComplianceFileLoad;
-- clear the old batches (then run BATCH_CREATION again, and FILE_LOAD / SOURCE_LOAD for their data)
DELETE FROM <META_DB>.ComplianceBatchOverride;
DELETE FROM <META_DB>.ComplianceRequestFileDetail;
DELETE FROM <META_DB>.ComplianceFileLoad;
DELETE FROM <META_DB>.ComplianceRequestControl;
UPDATE <META_DB>.ComplianceRequestInTake SET Last_Run_Dt_Key = NULL;
-- and the rows of those batches in the staging, core and export tables
```

## Batch status (`ComplianceRequestControl.Req_Stat`)

| `Req_Stat` | Open / closed | When |
|---|---|---|
| `REQUEST_CREATED` | Open | BATCH_CREATION created the batch (schedule or ad-hoc request); also when a carry-forward runs out |
| `SOURCE_DATA_READY` | Open | FILE_LOAD loaded the source's file into core (`Resolution_Ty = NEW_FILE`); a later file replaces it |
| `SOURCE_DATA_READY_NO_DATA` | Open | FILE_LOAD received the source's file and it had no data rows: the source had nothing to provide (`Resolution_Ty = NO_DATA`); the batch's core rows are removed |
| `SOURCE_DATA_CARRY_FORWARD` | Open | OVERRIDE_DECISIONS applied an approved `REUSE` override (`Resolution_Ty = CARRY_FORWARD`, `Reuse_Btch_ID`) |
| `EXCEPTION_PENDING` | Open | Older rows only: no step sets it any more |
| `REQUEST_COMPLETE` | Closed | Closed with data (`NEW_FILE` or `CARRY_FORWARD`); also after a late or corrected file with an approved override |
| `COMPLETED_WITH_EXCEPTION` | Closed | Closed while `EXCEPTION_PENDING` (older rows) |
| `DATA_NOT_PROVIDED` | Closed | Closed without data: `Resolution_Ty = NO_DATA` (the source sent a file with no data rows; closed by BATCH_CLOSE) or `MISSING` (no data at all by the close date; closed by BATCH_CLOSE, alert `SOURCE_MISSING_AT_CLOSE`). A final status like `REQUEST_COMPLETE` |

BATCH_CLOSE closes, once the SLA hold has passed, every open batch, with or without data: `SOURCE_DATA_READY` /
`SOURCE_DATA_CARRY_FORWARD` become `REQUEST_COMPLETE`, `SOURCE_DATA_READY_NO_DATA` becomes `DATA_NOT_PROVIDED`
(`NO_DATA`), and a batch still `REQUEST_CREATED` (nothing delivered, pulled or carried forward) becomes
`DATA_NOT_PROVIDED` (`MISSING`) with the alert `SOURCE_MISSING_AT_CLOSE` (emailed by NOTIFY: source, report dates,
batch). `DATA_NOT_PROVIDED` is a final status like `REQUEST_COMPLETE`. Not closed: batches of a run type in the
`hold` input and reopened batches still waiting for new data. So decide before the close run: a carry-forward
(`REUSE`) needs its override applied by OVERRIDE_DECISIONS before BATCH_CLOSE runs on the close date; after the
close, data still comes in with a `LATE_ARRIVAL` override or a `REOPEN`. A file that is rejected does not
change the batch status. `Allow_Zero_Rcd_Ind` is no longer used: a file with no data rows is always accepted as
the source's delivery. The loads themselves keep their own status (`ComplianceFileLoad.Load_Stat`: `PROMOTED`,
`SUPERSEDED`, `QUARANTINED`, ...).

**Reopening a closed batch (`REOPEN` override).** A late or corrected file can always be loaded into a closed
batch with an approved `LATE_ARRIVAL` / `CORRECTION` override (the batch stays closed). To open the batch again
instead, add a `REOPEN` row to `ComplianceBatchOverride` (`Override_Ty = 'REOPEN'`, `Apprvl_Stat = 'APPROVED'`,
`Valid_Thru_Dt_Key` = the last day it may be applied, `Rsn_Txt`, `Reviewed_By`). The next OVERRIDE_DECISIONS run
reopens any closed batch, with or without data:

| Closed as | Reopened as |
|---|---|
| `REQUEST_COMPLETE` / `NEW_FILE` | `SOURCE_DATA_READY` |
| `REQUEST_COMPLETE` / `CARRY_FORWARD` | `SOURCE_DATA_CARRY_FORWARD` (kept even if the `REUSE` override has expired) |
| `DATA_NOT_PROVIDED` / `NO_DATA` | `SOURCE_DATA_READY_NO_DATA` |
| `DATA_NOT_PROVIDED` / `MISSING` | `REQUEST_CREATED` |
| `COMPLETED_WITH_EXCEPTION` | `EXCEPTION_PENDING` |

`BATCH_REOPENED` is written to the timeline and the audit (emailed). The reopened batch takes files like any open
batch (no override needed; a resend replaces its rows), SOURCE_LOAD pulls an internal source's reopened batch
again, and the export and rules follow as usual. BATCH_CLOSE leaves it open until new data has arrived after the
reopen (a load, a pull or a carry-forward), then closes it on its next run; without new data it closes only with
`close-batch`. Each `REOPEN` row is applied once; to reopen the same batch again, add a new row.

Batches created before version 0.3.33 carry the earlier names; to rename them:

```sql
UPDATE <meta_db>.ComplianceRequestControl
SET Req_Stat = CASE Req_Stat WHEN 'PROMOTED' THEN 'SOURCE_DATA_READY'
                             WHEN 'CARRIED_FORWARD' THEN 'SOURCE_DATA_CARRY_FORWARD'
                             WHEN 'COMPLETED' THEN 'REQUEST_COMPLETE' END
WHERE Req_Stat IN ('PROMOTED', 'CARRIED_FORWARD', 'COMPLETED');
```

## Files on the NAS

Files are read from the NAS over SMB (`FILE_STORE` = `nas`, the default; library `smbprotocol`).

**Inbound folder** — `ComplianceSourceFileConfig.Src_File_Path` holds the folder:

| Written as | Server | Share | Folder |
|---|---|---|---|
| `\\nas01\Compliance\odr\in` or `//nas01/Compliance/odr/in` | `nas01` | `Compliance` | `odr/in` |
| `smb://nas01.corp.example/Compliance/odr/in` | `nas01.corp.example` | `Compliance` | `odr/in` |
| `nas://Compliance/odr/in` | the NAS Connection's host | `Compliance` | `odr/in` |

A path with its own server uses that server with the NAS Connection's login. `$env` works in the path.

**Where files go after a run** — into a subfolder of the folder they arrived in:

| Outcome | Moves to |
|---|---|
| Loaded to core | `<inbound folder>\Archive\` |
| Not loaded: unknown name, no batch or a closed batch, wrong column count, bad characters | `<inbound folder>\Error\` |
| Technical failure | stays in place and is retried by the next sweep |

- **Every pickup is a new load.** Each time FILE_LOAD finds a file in the inbound folder it gets a new row in
  `ComplianceFileLoad` (a new `Load_ID`) and is processed from the start, whatever happened to a file of the same
  name before: the same name and path may come back with updated data, after a rejection or after a load. For an
  open batch it replaces the batch's data; a closed batch still needs the usual override. Earlier rows stay as
  history. A load of the same file left unfinished by a run that stopped is closed as `FAILED_TECHNICAL`.
- There is no quarantine path and no archive path to configure. The subfolders are created when first
  needed and are never swept. Their names are the `ARCHIVE_FOLDER` / `ERROR_FOLDER` settings; with
  `ARCHIVE_BY_YEAR = true` a loaded file goes to `Archive/<year>/`.
- A file moved into `Archive` / `Error` never replaces one already there: the name gets `_1`, `_2`, ... before
  the extension (`X_1.txt`); its error log follows (`ErrorLog/X_1_errors.txt`).
- **Files still being copied:** a file changed in the last `NAS_MIN_AGE_SECONDS` (default 120) is left
  for the next sweep, so a half-written file is not loaded. The check compares the file's modified time
  on the NAS with the worker's clock.
- A file is identified by its path, size and modified time: delivering the same name again is a new
  load; a sweep that sees an untouched file again (one whose move failed) does not reload it.
- `ComplianceFileLoad.File_Share` holds `<server>/<share>`, `File_Path` the path inside the share and
  `File_Version` the size and modified time that identify one delivery of the file; audit
  rows show the file as `//server/share/path`.
- There is no S3 in this version: no bucket, key or object-version columns and no AWS file store.
  `FILE_STORE` = `local` (a folder on the worker) exists for development only.

## Sample metadata

A complete worked example - every metadata row for one project (three FDR sources, two internal sources that send
files and two pulled with a query; daily, monthly and ad-hoc run types; file checks; an existing table layout; the
export table; REUSE and REOPEN overrides) - is in `examples/`:
`cmsuniv_odag3_sample_metadata.xlsx` (one tab per table, in DDL order) and `cmsuniv_odag3_sample_metadata.sql`
(the same rows as Teradata `INSERT` statements).

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

`FILE_CHECK` looks at the files that are waiting in the inbound NAS folders and reports **every** problem of
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
| `Check_Cd` | The check (below) |
| `Check_Field_Nm` | For a field check: the field it checks (a name of `HEADER_NAMES`, else a staging column of the file); `*` (default) for the other checks |
| `Check_Val_Txt` | The value of the check (below) |
| `Include_Ind` | 1 = the check applies to the files the row covers; 0 = the check is switched off for them |
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
| `MIN_RECORD_LENGTH`, `MAX_RECORD_LENGTH` | `250` | Every data line has at least / at most that many characters (fields and delimiters, without quotes) |
| `MIN_ROWS`, `MAX_ROWS` | `100` | The file has at least / at most that many data rows |
| `MIN_LENGTH`, `MAX_LENGTH` (field) | `9` | The field's value has at least / at most that many characters |
| `MIN_VALUE`, `MAX_VALUE` (field) | `0`, `999999.99` | The field's value is a number not below / not above that value |
| `VALUE_IN` (field) | `Y\|N` (or `Y,N`) | The field's value is one of the listed values |
| `PATTERN` (field) | `[A-Z][0-9]{8}` | The field's whole value matches the regular expression |
| `DATE_FORMAT` (field) | `YYYYMMDD`, `MM/DD/YYYY`, `YYYY-MM-DD HH:MI:SS` | The field's value is a valid date / timestamp in that format |

The field checks need `Check_Field_Nm`; empty values are not field-checked (that is `COLUMN_VALUES REQUIRED`).

- **Inactive rows are ignored.** `Active_Ind` only says whether a row is in use.
- **Rows add up.** Every check set for the project (`*`), the table or the source applies to a file, so a source
  can get extra checks on top of the shared ones (e.g. `LENGTH` for every source and `MIN_LENGTH` on `MemberID`
  for source `140`).
- **The same check is replaced by its most specific row.** A check is its `Check_Cd` and `Check_Field_Nm`; for the
  same check the most specific active row wins: the file's table and source, then its table (any source), then its
  source (any table), then `*`. So a source can have its own `HEADER_NAMES` instead of the shared one, or its own
  limit for a field.
- **To switch a check off for one source**, add a row for that table, source and check (and field) with
  `Include_Ind = 0`.
- Two active rows cannot collide at the same level: a table, source, check and field have only one row.

Existing installations (from 0.3.39; the primary index gets `Check_Field_Nm`, so the table is rebuilt):

```sql
RENAME TABLE <META_DB>.ComplianceFileCheckConfig TO <META_DB>.ComplianceFileCheckConfig_Old;
-- create ComplianceFileCheckConfig from sql/schema.sql, then
INSERT INTO <META_DB>.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt,
       Include_Ind, Active_Ind, Created_Dtts, Created_By, Updated_Dtts, Updated_By)
SELECT Project_Cd, Table_Nm, Src_ID, Check_Cd, '*', Check_Val_Txt, Include_Ind, Active_Ind, Created_Dtts, Created_By,
       Updated_Dtts, Updated_By FROM <META_DB>.ComplianceFileCheckConfig_Old;
DROP TABLE <META_DB>.ComplianceFileCheckConfig_Old;
```
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

**Large files.** The load streams the file: it is read once (its SHA-256 is computed while it is copied from the
NAS) and sent to the staging table in batches.

| Setting | Default | What it does |
|---|---|---|
| `INSERT_BATCH_ROWS`, `INSERT_BATCH_BYTES` | `2000`, `500000` | Rows sent to Teradata per batch: a batch ends at either limit. Larger batches mean fewer round trips; try e.g. `10000` / `4000000` with your largest file in DEV before raising them in PROD |
| `LOAD_ENGINE` | `BATCH` | `FASTLOAD` loads the file with Teradata FastLoad into a work table of the load (`<staging table>_W<Load_ID>`, in the staging database), then copies it into the staging table with one `INSERT ... SELECT` and drops the work table. The database account needs CREATE TABLE and DROP TABLE in the staging database. The driver falls back to ordinary batches when FastLoad is not possible. Worth it for files of several hundred thousand rows and more; measure in DEV first. A value that does not fit is reported without its line number |
| `CHECK_ON_LOAD` | `false` | `true` runs the `FILE_CHECK` content checks inside `FILE_LOAD` on the file the load has already downloaded, so a big file is not downloaded and read twice. A file with a problem is rejected as `FILE_CONTENT_CHECK_FAILED`, every problem goes to its error log, and it moves to `Error/`. The separate `FILE_CHECK` step still works as before |

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
| `File_Processed_Date` | When the load finished, in Central time |
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

- **Airflow:** its own DAG, `OSSTD_CORE_CMS_COMPLIANCE_FILE_CHECK`, with its own Variable
  `compliance_file_check_config_var` (the same keys as the file load Variable). It is unscheduled in the
  examples. A project that wants the check gives it the schedule and lists
  `OSSTD_CORE_CMS_COMPLIANCE_FILE_LOAD` in its `trigger_dags`, so the load starts only when the check
  succeeded; `"fail_on_problems": false` in the Variable starts the load anyway and keeps the check as a
  report. A project that does not want it leaves the DAG unscheduled and schedules the file load DAG.
  One run by hand: trigger the DAG, or `OSSTD_CORE_CMS_COMPLIANCE_RUN_MODULE` with `{"module": "FILE_CHECK", "project": "..."}`.

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
  `core_schema_nm`, `core_table_nm`) with the batch and load ids, and its export table (`exp_schema_nm`,
  `exp_table_nm`) when the table has one (`EXPORT_LOAD`).
- **Airflow:** the rules have their own DAG, `OSSTD_CORE_CMS_COMPLIANCE_FILE_RULES`, with its own
  Variable. The file-load DAG starts it after each load through `trigger_dags` (without waiting for
  it); it can also be triggered by hand (`{"project": "CMS_UNIVERSE"}`, or `{}` for every project) or
  given its own `schedule_interval` as a safety net for a missed trigger. It starts the notify DAG when
  it is done, so a failed rule is emailed.


## Failure tickets and emails

- Every task has `on_failure_callback`: a failed task opens a ServiceNow incident through
  `snow.snow_integrations.create_incident(context, assignment_group)`. If the `snow` package is not
  installed on the workers, a warning is logged instead.
- With `email_recipient` set, each DAG ends with `success_email` (no step failed) and `fail_email` (a
  step failed), subjects `Airflow EDEG-Compliance <load_env> Success|Failure: <dag id> DAG`. They use
  Airflow's own email settings.
- These are about the DAG run. Business events (a rejected file, a missing source, an override) are
  emailed by the `NOTIFY` step to the addresses in its `DEFAULT_NOTIFY_EMAILS` setting. The file
  config holds no recipients or subjects; a project that sends its own notifications leaves `NOTIFY` unscheduled.

## Scheduled batches: when they are created and for which dates (metadata)

Scheduled batch creation is configured in the metadata, with codes; SQL is only a fallback for what the codes
cannot express. Nothing about it is in the DAG, the Airflow Variable or the code. The scheduler only starts
`BATCH_CREATION` for a project, normally once a day, and a run started by hand behaves the same way.

| Question | Table | Columns (codes) | Fallback |
|---|---|---|---|
| **When** are batches created? | `ComplianceRunType` (run type) | `Sched_Cd`, `Sched_Day_Cd`, `Sched_Week_Cd`, `Sched_Months_Txt`, `Sched_Window_Days` | `Batch_Schedule_Sql_Txt` |
| **Which sources** get batches of a run type? | `ComplianceDataSetSourceXwalk` (project, table, source; `Run_Ty` lists the run types) | — | — |
| **Which report dates** does a batch get? | `ComplianceRunTypePeriod` (project, table or `*`, run type), or `Rpt_Period_Cd` on the crosswalk row | `Rpt_Period_Cd` | `Rpt_Dt_Sql_Txt` |
| **Irregular dates** (an extra or moved submission) | `ComplianceBatchCalendar` (optional) | one row per date and report period | — |

### When: schedule codes on `ComplianceRunType`

| Batches are due | `Sched_Cd` | `Sched_Day_Cd` | `Sched_Week_Cd` | `Sched_Months_Txt` |
|---|---|---|---|---|
| Every day | `DAILY` | | | |
| Every Sunday | `WEEKLY` | `SUN` (`MON` ... `SUN`) | | |
| The 5th of each month | `MONTHLY` | `5` (a day past the month's end = its last day) | | |
| The last day of each month | `MONTHLY` | `LAST` | | |
| The 3rd Tuesday of each month | `MONTHLY` | `TUE` | `3` (`1` ... `4`, `LAST`) | |
| The 1st of each quarter | `MONTHLY` | `1` | | `1,4,7,10` |
| The Sunday before the 3rd Tuesday | `MONTHLY` | `TUE` | `3` | | 

`Sched_Offset_Days` (default 0) moves the date the codes name: `-2` with the 3rd Tuesday = the Sunday before it
(the 13th to the 19th, so sometimes the 2nd Sunday, sometimes the 3rd). Report dates are counted from the moved
date. Between -31 and 31.

`Sched_Window_Days` (default 1): the batch can still be created that many days from its scheduled date, for the
same report dates, so a missed run catches up (e.g. `5` for the 3rd Tuesday: Tuesday to Saturday). One batch is
made per scheduled date, never one per day of the window.

### Which sources: one crosswalk row per table and source

`Run_Ty` lists the run types of the source, e.g. `DAILY,WEEKLY,MNT` (only the listed run types: a run type added
later applies to no source until it is listed). The version and effective dates are on the row. A row for **one**
run type replaces the list's entry for that run type while that row is effective: its own `Cmplnc_Vrsn`, effective
dates or `Rpt_Period_Cd`; with `Include_Ind = 0` it switches that run type off for the source. A row with a single
run type and no list is a normal row, so existing crosswalks keep working unchanged.

```sql
INSERT INTO <META_DB>.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Cmplnc_Vrsn)
VALUES ('CMSUNIV', 'ODAG3', '51', 'DAILY,WEEKLY,MNT', DATE '2026-01-01', '2200');
-- source 140 has no weekly batches
INSERT INTO <META_DB>.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Cmplnc_Vrsn, Include_Ind)
VALUES ('CMSUNIV', 'ODAG3', '140', 'WEEKLY', DATE '2026-01-01', '2200', 0);
```

### Which dates: `ComplianceRunTypePeriod` (or `Rpt_Period_Cd` on the crosswalk)

The report dates of a run type for a project: `Project_Cd`, `Table_Nm` (`*` = every table), `Run_Ty`,
`Rpt_Period_Cd`. The period used for a batch is the first found of: the crosswalk row's `Rpt_Period_Cd`, its
`Rpt_Dt_Sql_Txt`, the table's `ComplianceRunTypePeriod` row, the project's `*` row. A SCHEDULED run type with a
schedule code and no period anywhere is a validate-config error. An ADHOC run type with a schedule gets scheduled
batches for every crosswalk row that finds a period (set none for it when only some sources are scheduled, and put
`Rpt_Period_Cd` on those rows).

```sql
INSERT INTO <META_DB>.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd) VALUES ('CMSUNIV', '*', 'DAILY', 'PREV_DAY');
INSERT INTO <META_DB>.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd) VALUES ('CMSUNIV', '*', 'WEEKLY', 'PREV_CALENDAR_WEEK');
INSERT INTO <META_DB>.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd) VALUES ('CMSUNIV', '*', 'WEEKLY2', 'RANGE(WEEK,5,1)');
```

Codes, counted from the scheduled date: `PREV_DAY`, `SAME_DAY`, `PREV_CALENDAR_WEEK` (Monday to Sunday),
`PREV_CALENDAR_MONTH`, `CURRENT_CALENDAR_MONTH`, `ROLLING_1_MONTH`, `PREV_CALENDAR_QUARTER`, `PREV_CALENDAR_YEAR`,
`PREV_N_DAYS(n)`, `PREV_WEEK_SAME_DAY(n)`, `ANNUAL_WINDOW(MM-DD,MM-DD[,MM-DD])`, and for any other range
`RANGE(unit, back, length)`: `length` units starting `back` units before the scheduled date's unit; units `DAY`,
`WEEK` (Monday to Sunday), `MONTH`, `QUARTER`, `YEAR`. Examples on Sunday 2026-10-18: `RANGE(DAY,1,1)` 10-17;
`RANGE(DAY,35,7)` 09-13 to 09-19; `RANGE(WEEK,1,1)` 10-05 to 10-11; `RANGE(WEEK,5,1)` 09-07 to 09-13;
`RANGE(MONTH,1,1)` September; `RANGE(MONTH,3,3)` July to September; `RANGE(MONTH,0,1)` October; `RANGE(QUARTER,1,1)`
July to September; `RANGE(YEAR,1,1)` 2025. `FIRST_DAYS(unit, back, days)` / `LAST_DAYS(unit, back, days)`: the
first / last days of the unit `back` units before (units `WEEK`, `MONTH`, `QUARTER`, `YEAR`; at most 7 / 28 / 90 /
365 days): `LAST_DAYS(MONTH,1,14)` on 2026-07-05 = 06-17 to 06-30, on 2026-04-12 = 03-18 to 03-31. Example: MNT on the 3rd Tuesday of October with `PREV_CALENDAR_MONTH`
gets 09-01 to 09-30.

Existing installations: `Run_Ty` is in the crosswalk's primary index, so it cannot be widened in place; the table
is copied once. First run the `ComplianceDataSetSourceXwalk` CREATE TABLE of `sql/schema.sql` with the name
`ComplianceDataSetSourceXwalk_N`, then:

```sql
INSERT INTO <META_DB>.ComplianceDataSetSourceXwalk_N
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt,
   Rpt_Period_Cd, Include_Ind, Active_Ind, Created_Dtts, Created_By, Updated_Dtts, Updated_By)
SELECT Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt,
       Rpt_Period_Cd, 1, Active_Ind, Created_Dtts, Created_By, Updated_Dtts, Updated_By
FROM <META_DB>.ComplianceDataSetSourceXwalk;
RENAME TABLE <META_DB>.ComplianceDataSetSourceXwalk TO <META_DB>.ComplianceDataSetSourceXwalk_Old;
RENAME TABLE <META_DB>.ComplianceDataSetSourceXwalk_N TO <META_DB>.ComplianceDataSetSourceXwalk;
-- and ComplianceRunTypePeriod from sql/schema.sql
```

### Irregular dates: `ComplianceBatchCalendar` (optional)

One row per run type, request date and report period (`Project_Cd`, `Table_Nm` (`*` = every table), `Run_Ty`,
`Req_Dt_Key`, `Rpt_Start_Dt_Key`, `Rpt_End_Dt_Key`): on `Req_Dt_Key` BATCH_CREATION creates a batch for those
report dates for every source of the run type in the crosswalk, next to the schedule. Rows for a whole year can be
written with one `INSERT ... SELECT` from `sys_calendar.calendar`. A missed day is caught up with `as_of`.

### Fallback: the SQL columns

Used only when the run type has no `Sched_Cd` (`Batch_Schedule_Sql_Txt`) or the crosswalk row no `Rpt_Period_Cd`
(`Rpt_Dt_Sql_Txt`); existing rows keep working. With a code schedule, `Rpt_Dt_Sql_Txt` can use `{period_dt}` (the
scheduled date). Existing installations add the columns:

```sql
ALTER TABLE <META_DB>.ComplianceRunType ADD Sched_Cd VARCHAR(10), ADD Sched_Day_Cd VARCHAR(5), ADD Sched_Week_Cd VARCHAR(5),
  ADD Sched_Months_Txt VARCHAR(40), ADD Sched_Window_Days SMALLINT DEFAULT 1 NOT NULL;
ALTER TABLE <META_DB>.ComplianceRunType ADD Sched_Offset_Days SMALLINT DEFAULT 0 NOT NULL;
ALTER TABLE <META_DB>.ComplianceDataSetSourceXwalk ADD Rpt_Period_Cd VARCHAR(60);
-- and ComplianceBatchCalendar from sql/schema.sql when it is used
```

Which tables and sources get batches of a run type is the crosswalk itself: a source gets batches of the run
types its row lists (or of its row's single run type).

### SQL fallback for when: `ComplianceRunType.Batch_Schedule_Sql_Txt`

A single `SELECT` that returns **one row on a day batches are due and no row on any other day**.

- `{run_date}` is the run date (today in Central time, or `as_of`); `{project_cd}` and `{run_ty}` are
  the project and run type of the run. Use `{run_date}`, not `CURRENT_DATE`, so a re-run for a missed day works.
- What the row contains is up to you, and **every column it returns can be used by name in the report
  date statements** — for example a `period_dt` the dates are counted from.
- A run type without a statement gets no scheduled batches (only a run that passes `period`, below).

Examples (Teradata):

| Batches are due | `Batch_Schedule_Sql_Txt` |
|---|---|
| Every day | `SELECT 1 AS due` |
| Every Monday | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE TD_DAY_OF_WEEK(d) = 2` |
| First 5 days of the month | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE EXTRACT(DAY FROM d) <= 5` |
| Only the Sunday before the 3rd Tuesday of the month | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE TD_DAY_OF_WEEK(d) = 1 AND EXTRACT(DAY FROM d + 2) BETWEEN 15 AND 21` |
| Any day of the 4th quarter | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE EXTRACT(MONTH FROM d) >= 10` |
| Any day of the second month of the second quarter (May) | `SELECT d AS run_dt FROM (SELECT CAST({run_date} AS DATE) AS d) x WHERE EXTRACT(MONTH FROM d) = 5` |

### SQL fallback for which dates: `ComplianceDataSetSourceXwalk.Rpt_Dt_Sql_Txt`

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

Both statements must be a single `SELECT` (or `WITH ... SELECT`) and runs with the framework's database account. Placeholders are bound as
parameters, so write `CAST({run_date} AS DATE)`; a placeholder the run does not have is an error that
lists the available ones. Do not put a literal `?` in the text: Teradata reads it as a parameter. `validate-config` runs the schedule statements for today (or `as_of`)
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
FROM (SELECT CAST({run_date} AS DATE) + (3 - TD_DAY_OF_WEEK(CAST({run_date} AS DATE))) AS tue) x
WHERE TD_DAY_OF_WEEK(CAST({run_date} AS DATE)) <= 5
  AND (EXTRACT(DAY FROM tue) - 1) / 7 + 1 = 3
```

`Rpt_Dt_Sql_Txt` of the crosswalk rows:

| Crosswalk rows | Report dates | `Rpt_Dt_Sql_Txt` |
|---|---|---|
| CDAG / ODAG tables, `MNT` | previous calendar month | `SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| CDAG / ODAG tables, `WKL` | 60 days ending the last Saturday | `SELECT p - 62 AS rpt_start, p - 3 AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| `SNPCC1`, `MNT` and `WKL` | last day of the previous month | `SELECT p - EXTRACT(DAY FROM p) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| `FA1`, `MNT` | last 14 days of the previous month | `SELECT p - EXTRACT(DAY FROM p) - 13 AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d` |
| `FA2`, `MNT` | Jan 1 → Jan 31 | `SELECT CAST(TRIM(yr) || '-01-01' AS DATE) AS rpt_start, CAST(TRIM(yr) || '-01-31' AS DATE) AS rpt_end FROM (SELECT EXTRACT(YEAR FROM ADD_MONTHS(CAST({period_dt} AS DATE), -1)) AS yr) d` |
| `FA3`, `MNT` | Nov 1 → Dec 31 of the year before | `SELECT CAST(TRIM(yr - 1) || '-11-01' AS DATE) AS rpt_start, CAST(TRIM(yr - 1) || '-12-31' AS DATE) AS rpt_end FROM (SELECT EXTRACT(YEAR FROM ADD_MONTHS(CAST({period_dt} AS DATE), -1)) AS yr) d` |
| `FA4`, `MNT` and `WKL` | Nov 1 of the year before → Jan 31 | `SELECT CAST(TRIM(yr - 1) || '-11-01' AS DATE) AS rpt_start, CAST(TRIM(yr) || '-01-31' AS DATE) AS rpt_end FROM (SELECT EXTRACT(YEAR FROM ADD_MONTHS(CAST({period_dt} AS DATE), -1)) AS yr) d` |

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

The sources of one table can have staging (and core) tables with different column names, e.g. `Src_Data_Key` in
the file staging table and `Source_ID` in an internal source's: map both. A mapped column a source's tables do not
have is skipped for that source; validate-config reports a mapped column that is in none of the table's staging or
core tables (`TARGET_COLUMN`).

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
| `BATCH_ID` | The batch ID in a table whose batch column has another name than `Btch_ID` (e.g. `Batch_ID`, `BatchID`); that column is then the table's batch column (staging, core or export) |
| `RUN_TYPE`, `VERSION`, `SOURCE_ID` | The batch's run type, the compliance version the batch was created with (`ComplianceRequestControl.Cmplnc_Vrsn`), the batch's `Src_ID` |
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
- **Core holds one load per batch and source.** When a file is loaded for a batch that already has rows of that
  source in the core table, those rows are deleted by `Btch_ID` and the source column (the one mapped to
  `SOURCE_ID`, required in the staging and core table) and the new rows are inserted, in one transaction. The core table
  needs no flag or end-date column; rows of other batches are not touched.
- **Staging is cleared before a load by run type**: every staging row of the file's run type is removed,
  then the file is loaded (the staging table needs a `RUN_TYPE` column for this, as in the example below).
  Rows of other run types are not touched. A staging table without a run type column is cleared by batch and source.
- **`SQL` columns.** `Value_Txt` is a single `SELECT` that returns one row with one column, for example
  `SELECT CURRENT_DATE` or a lookup such as `SELECT Audit_ID FROM <db>.Audit_Run WHERE Btch_ID = {btch_id} AND Src_ID = {src_id}`
  (a `Btch_ID` covers every source of the table: add `{src_id}`, or use `{req_id}`; `validate-config` warns).
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

The run categories are `SCHEDULED` (batches created on a schedule for a period; was `ROUTINE`) and
`ADHOC` (batches requested through `ComplianceRequestInTake`). The two ways of creating batches can be mixed:

- An `ADHOC` run type with a `Batch_Schedule_Sql_Txt` gets scheduled batches for its crosswalk rows that have a
  `Rpt_Dt_Sql_Txt`; its rows without one get batches from `ComplianceRequestInTake` only. So one ad-hoc run type can
  run daily for some sources and on request (other dates) for others.
- `ComplianceRequestInTake` takes any active run type, `SCHEDULED` ones too (e.g. an extra `MNT` batch for an earlier
  month).
- A request whose source, run type and report dates already have an open batch made by the schedule or another
  request uses that batch (no second batch); a closed batch gets a new one (a re-request). A request's own window
  (`Req_Start_Dt_Key` to `Req_End_Dt_Key`) still makes its batch on each day. The schedule skips report dates
  that already have a batch.
- Release follows the run type's category, whichever way the batch was made: `SCHEDULED` on the due date, `ADHOC`
  with `release=true`.
- `BATCH_CREATION` with `run_type` runs the schedule when that run type has one, and only that run type's
  requests.

## Environment in database names (`$env`)

The staging and core database names and the inbound path of `ComplianceSourceFileConfig` can carry
a `$env` token, so one set of configuration rows works in every environment (the GRE convention):

| Authored | DEV | TEST | QA | PROD / UAT |
|---|---|---|---|---|
| `CMS_$ENV_STG` | `CMS_DEV_STG` | `CMS_TEST_STG` | `CMS_QA_STG` | `CMS_STG` |
| `cms_core_$env_t` | `cms_core_dev_t` | `cms_core_test_t` | `cms_core_qa_t` | `cms_core_t` |
| `//nas01/Compliance/$env/odr/in` | `//nas01/Compliance/dev/odr/in` | `//nas01/Compliance/test/odr/in` | `//nas01/Compliance/qa/odr/in` | `//nas01/Compliance/odr/in` |

- Columns: `Stg_Schema_Nm`, `Core_Schema_Nm`, `Src_File_Path`.
- The token matches in any casing and is replaced in its own casing (`$env` → `dev`, `$ENV` → `DEV`,
  `$Env` → `Dev`). PROD and UAT replace it with nothing; a doubled underscore left in a database name
  collapses (paths are left as they are, so prefer a database-style name where PROD has no suffix).
- Keep the token in the middle of a database name: at the very end or start it leaves the underscore
  behind in PROD and UAT (`CMS_STG_$ENV` → `CMS_STG_`), exactly as GRE does.
- The environment is the `ENVIRONMENT` setting (the Variable's `load_env`); `ENV_VALUE` overrides the replacement text for
  that environment (e.g. `ENV_VALUE=uat` where UAT databases do carry a suffix).

## Teradata specifics

**Tables.** `sql/schema.sql` defines 15 MULTISET tables for `meta_db`, created separately: the 14 framework tables plus
`ComplianceLock`. Identity columns are `GENERATED ALWAYS AS IDENTITY`; there are no foreign keys or
CHECK constraints (the validator checks references).

**Staging and core tables** are created by each project, in the databases named in
`ComplianceSourceFileConfig` (`Stg_Schema_Nm` / `Core_Schema_Nm` are Teradata database names):

Their layout is free when it is described in `ComplianceTargetColumnConfig` (above). Without rows there, they
follow the framework's own column names:

- Staging: the business columns in file order, then `btch_id VARCHAR(250)`, `load_id BIGINT`,
  `src_file_nm VARCHAR(1024)`, `stg_load_dtts TIMESTAMP(6) WITH TIME ZONE`. File values arrive as
  text: declare business columns VARCHAR, or types Teradata converts from text implicitly (a value that
  does not convert quarantines the file as `FILE_PARSE_ERROR`).
- Rejected rows: the `FILE_PARSE_ERROR` message names the file line, both for bytes that are not valid
  in `FILE_ENCODING` and for a value Teradata refuses (untranslatable character, bad number or date,
  overflow). Teradata does not say which row of a batch failed, so the framework re-sends parts of the
  failed batch (about a dozen trial inserts, each rolled back) to find it.
- Core: the business columns, then `btch_id`, `load_id`, `current_ind SMALLINT`,
  `load_dtts TIMESTAMP(6) WITH TIME ZONE`, `end_dtts TIMESTAMP(6) WITH TIME ZONE`.

**Differences from the PostgreSQL version**

| PostgreSQL | Teradata |
|---|---|
| Session advisory locks | Rows in `ComplianceLock` (insert = acquire, delete = release) |
| A crashed run's locks vanish with its session | They stay until they expire (`LOCK_TTL_MINUTES`, default 240). `locks` lists them, `release-lock --key` removes one, `health` shows them. Keep the TTL above the longest step. |
| `FOR UPDATE SKIP LOCKED` row claims | Each notification / intake request is claimed with its own lock row |
| Partial unique indexes (one open override per batch and type; one active file config per source) | Reported by `validate-config` (`OVERRIDE_DUPLICATE`, `FILE_CONFIG_DUPLICATE`); the approval templates refuse a duplicate |
| `COPY` into staging | Batched parameterised inserts |
| Report periods as SQL | Computed in Python (`batches.PERIODS`); `--period-file` still takes Teradata SQL |
| Spark load engine | Not included |
| Files in S3 (`S3_Src_File_Path`, `S3_Bucket`, `S3_Key`, `S3_Version_Id`, `S3_ETag`) | Files on the NAS (`Src_File_Path`, `File_Share`, `File_Path`, `File_Version`); `FILE_LOAD` is scoped by `--share` / `--folder` / `--file` |

Reads use `LOCKING ROW FOR ACCESS`; every change to a batch is made under that batch's lock row.
String comparisons follow the session's transaction mode (Teradata mode is not case specific).

## File rules through the GRE

With `RULE_ENGINE=gre` and `GRE_ENTRYPOINT=compliance_framework.export:run_gre`, each
`ComplianceRuleBinding` of a file runs that GRE `rule_group` (and `rule_variant`; `*` = all) through the
GRE's `run_rules()`, with `run_key = CBF_LOAD_<Load_ID>` and the file's context as `run_params`
(`{btch_id}`, `{load_id}`, `{stg_schema_nm}`, `{stg_table_nm}`, `{core_schema_nm}`, `{core_table_nm}`, `{exp_schema_nm}`, `{exp_table_nm}`, `{exp_btch_id}` (only with an export table), `{project_cd}`, `{table_nm}`, `{src_id}`,
`{run_ty}`, `{rpt_start_dt_key}`, `{rpt_end_dt_key}`, `{req_dt_key}`). The verdict of each rule is read
from `<gre meta_db>.gre_results`: `PASS` / `WARN` pass, `FAIL` marks the file's rules as failed (or as
warnings under ANNOTATE), anything else is a technical failure that is retried. This happens in the
`OSSTD_CORE_CMS_COMPLIANCE_FILE_RULES` DAG, after the core load (above). Use `RULE_ENGINE=none` to run no rules.

The `gre` section of `compliance_file_rules_config_var` is passed on to `run_rules()`: `project_name`, `text_params` and
`extra_filters` as written, and `run_params` merged with the file's context (the file's values win).
`text_params` and `extra_filters` are the same for every file of the Variable: where they name a run type
(e.g. `{"RUNTYPE": "MNT"}`, `{"run_ty": "MNT"}`), use a `runs` entry per project with its own `gre`
values, or a second rules DAG with its own Variable. The rules run on the framework's Teradata connection.

## Validation status

- The framework logic (batch creation, intake, file load, promotion, overrides, close, notifications,
  locks) and the GRE bridge pass the framework's test suite when run against a stand-in for the
  `teradatasql` driver backed by PostgreSQL. With Airflow 3.3.2 (Task SDK 1.3.2, standard provider 1.20.1, smtp
  provider 3.1.1) every DAG file parses without import errors or deprecation warnings, and the admin DAG ran with
  `dag.test()` (Variable and Connections read through `airflow.sdk`). The step DAGs need Teradata and were not run
  end to end in Airflow 3.
- It has **not** been run against a real Teradata system. Before production, on a development Teradata
  system: create the tables from `sql/schema.sql`, run `validate-config`, then one full cycle (`BATCH_CREATION`, `FILE_LOAD` of a good
  and a bad file, `BATCH_CLOSE`, `NOTIFY`) with the step DAGs or `OSSTD_CORE_CMS_COMPLIANCE_RUN_MODULE`, and check the tables.
- The NAS store was checked against a local SMB test server for connect, list, read and download only,
  and with a stand-in for the rest. Moving files (`Archive` / `Error`), creating the subfolders and the
  modified-time check have **not** been run against your NAS: verify them with one good and one bad file.
- The ServiceNow callback and the email tasks ran with stand-ins for `snow` and the email backend.
