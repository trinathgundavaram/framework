# Compliance Batch Framework: Airflow + Teradata

A self-contained copy of the compliance batch framework that runs from Airflow DAGs with its metadata,
staging and core tables in **Teradata**. Nothing outside `airflow/` is used or changed. It follows the
layout and connection style of the GRE rules engine's Airflow package (`DQ_COMPLETE_REPO/airflow`) and
the team's DAG conventions (one DAG file, one DAG id, one `<name>_config_var` Variable).

```
airflow/
  osstd_core_cms_compliance_batch_creation.py      one DAG file per step ...
  osstd_core_cms_compliance_file_load.py
  osstd_core_cms_compliance_file_rules.py
  osstd_core_cms_compliance_override_decisions.py
  osstd_core_cms_compliance_batch_close.py
  osstd_core_cms_compliance_notify.py
  osstd_core_cms_compliance_run_module.py          ... one generic DAG that runs any step
  osstd_core_cms_compliance_admin.py               ... and one for validate-config, health, locks
  compliance_framework/
    dag_factory.py                builds the DAGs (schedule, tasks, triggers, emails, ServiceNow callback)
    compliance_task.py            Variable + Connections -> the framework
    run_framework.py              run_step() / run_command(): the framework in-process, no Airflow
    connection_factory.py         Teradata connection (TERADATA_*) and NAS / SMB settings (NAS_*)
    db.py                         Teradata layer: transactions, lock table
    gre_bridge.py                 file rules through the GRE rules engine
    batches.py ingest.py load.py rules.py overrides.py closing.py audit.py config.py modules.py
    adapters.py app.py cli.py common.py settings.py
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
   | `FILE_LOAD` | `OSSTD_CORE_CMS_COMPLIANCE_FILE_LOAD` | `compliance_file_load_config_var` |
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
   waiting): in the examples `FILE_LOAD` starts `FILE_RULES`, `OVERRIDE_DECISIONS` and `NOTIFY`; the
   others start `NOTIFY`. Each DAG can also have its own `schedule_interval`.
4. A task runs one step in-process. It fails on a framework error (exit code 2), and on "completed
   with problems" (exit code 1) where the step is configured that way; otherwise the outcome is the
   task's XCom.

No `.env` file and no AWS Secrets Manager: credentials come from the Airflow Connections only.

## Deploying

1. Copy the `osstd_core_cms_compliance_*.py` files and the `compliance_framework/` folder into the DAGs
   folder, side by side.
2. Install `compliance_framework/requirements.txt` in the workers' environment. Airflow 2.4+, Python 3.9+.
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
  "load_duplicate": "no",
  "settings": {"FILE_STORE": "nas", "ARCHIVE_FOLDER": "Archive", "ERROR_FOLDER": "Error"},
  "trigger_dags": ["OSSTD_CORE_CMS_COMPLIANCE_FILE_RULES", "OSSTD_CORE_CMS_COMPLIANCE_NOTIFY"]
}
```

Keys every Variable can have:

| Key | Meaning |
|---|---|
| `meta_db` (required) | Teradata database holding the framework tables |
| `td_conn_var` | Airflow Connection of Teradata. Falls back to `connection_id`, then `gre.connection_id`, then `compliance_teradata` |
| `wdc_comp_oper_nas_var` | Airflow Connection of the NAS file server. Required where files are loaded (`FILE_LOAD`, and the generic and admin DAGs when they touch files) |
| `load_env` | `DEV`, `TEST`, `QA`, `PROD`, ...: shown in the email subjects, and what `$env` in configured database names and paths resolves to (below); falls back to `environment`, then `gre.environment`, else `DEV` |
| `schedule_interval` | Cron expression of the DAG; leave out for a DAG that is only triggered |
| `schedule_timezone` | Time zone of the schedule (default `America/Chicago`; follows daylight saving) |
| `trigger_dags` | DAG ids to start when the step succeeds; `project` and `as_of` given to this run are passed on |
| `email_recipient` | Who gets the DAG's success / failure emails (`success_email`, `fail_email` tasks). Without it the DAG has no email tasks |
| `snow_assignment_group` | ServiceNow assignment group of the incident opened when a task fails (default `D&AE - EDE Govt Compliance`) |
| `owner`, `tags` | DAG owner (default `oss`) and tags |
| `settings` | Framework settings by name. Common: `BUSINESS_TZ`, `LOCK_TIMEOUT_SECONDS` (300), `LOCK_TTL_MINUTES` (240); plus the step's own, below |
| `fail_on_problems` | `true` / `false`: whether "completed with problems" (exit code 1) fails the task. Default: true, except `FILE_LOAD` and `OVERRIDE_DECISIONS` (a bad file or override is audited and emailed, not a task failure) |
| `runs` | Optional list of scopes: one task per entry, each with its own `project` / `run_type` / `period` / ... (and `settings`, `gre`). Without it the DAG has one task, scoped by the Variable's own keys |
| `log_level` | `INFO` (default), `DEBUG`, ... |

Keys by step:

| Step | Scope keys | Settings worth setting |
|---|---|---|
| `BATCH_CREATION` | `project` (required), `run_type`, `period` (`PREV_DAY`, `PREV_CALENDAR_MONTH`, `CURRENT_CALENDAR_MONTH`, `PREV_CALENDAR_WEEK`, ...), `table`, `lookback_days`, `lookback_weeks` | — |
| `FILE_LOAD` | `project` (leave out for every project), `load_duplicate` (`yes` / `no`, default `no`) | `FILE_STORE` (`nas`), `ARCHIVE_FOLDER`, `ERROR_FOLDER`, `NAS_MIN_AGE_SECONDS` |
| `FILE_RULES` | `project`; `gre` (the GRE's own Airflow Variable, as it is: `connection_type`, `connection_id`, `environment`, `meta_db`, `meta_connection`, `project_name`, `run_params`, `text_params`, `extra_filters`, `log_level`, `max_parallel_rules`; plus `package_dir`, the folder holding the GRE's `run_rules.py`, default `<dags>/rules_engine`) | `RULE_ENGINE` (`gre` / `none`), `GRE_ENTRYPOINT`, `FILE_RULES_MODE` |
| `OVERRIDE_DECISIONS` | `project` | — |
| `BATCH_CLOSE` | `project`, `table`, `run_type` | — |
| `NOTIFY` | `project` (leave out to send every pending event, including those of no project) | `NOTIFY_BACKEND` (`airflow` = Airflow's email backend, `ses`, `log`), `NOTIFY_FROM_EMAIL` (ses), `DEFAULT_NOTIFY_EMAILS` |

A trigger's configuration overrides the Variable for that run: any scope key above, `as_of` (an ISO
date or timestamp used as "now", for a missed day), `settings` and `gre`.

## DAGs

- **Step DAGs** — one task named after the step (or one per `runs` entry), then a `trigger_<dag id>`
  task per `trigger_dags` entry, then `success_email` / `fail_email`. `max_active_runs` is 1 and tasks
  do not retry (`retries: 0`); every step is idempotent, so the next run picks up. DAGs that overlap are
  safe (locks, below).
- **`OSSTD_CORE_CMS_COMPLIANCE_RUN_MODULE`** — the generic DAG. Its `module` parameter names the step
  and the other parameters are that step's (`project`, `run_type`, `period`, `table`, `lookback_days`,
  `lookback_weeks`, `load_duplicate`, `as_of`). A parameter the module does not take is refused. Its
  Variable holds the connections, `meta_db` and the settings of every step; nothing about a step is
  fixed in it. Examples:
  - `{"module": "BATCH_CREATION", "project": "CMS_UNIVERSE", "run_type": "MNT", "period": "PREV_CALENDAR_MONTH"}`
  - `{"module": "FILE_LOAD", "project": "CMS_UNIVERSE", "load_duplicate": "yes"}`
  - `{"module": "FILE_RULES"}`, `{"module": "BATCH_CLOSE", "project": "CMS_UNIVERSE"}`, `{"module": "NOTIFY"}`
- **`OSSTD_CORE_CMS_COMPLIANCE_ADMIN`** — `validate-config`, `health`, `test-connection`, `show-config`,
  `locks`, `release-lock`, `close-batch`:
  `{"command": "close-batch", "args": ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]}`.
- A missing or invalid Variable never breaks parsing: the DAG is still there, unscheduled, and its task
  fails with the reason when run.
- More trigger examples: [`examples/trigger_configurations.json`](examples/trigger_configurations.json).

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
| Not loaded: unknown name, no batch or a closed batch, wrong column count, bad characters, an empty file where one is not allowed, a name already loaded (unless `load_duplicate` = `yes`) | `<inbound folder>\Error\` |
| Technical failure | stays in place and is retried by the next sweep |

- **Duplicate file names (`LOAD_DUPLICATE`).** A file whose name was already loaded is rejected by
  default (`FILE_REJECTED_DUPLICATE`, moved to `Error`). With `LOAD_DUPLICATE` = `yes` it is loaded like
  any other file: while the batch is open it replaces the batch's data; a closed batch still needs the
  usual override. A name whose earlier delivery was rejected can always be delivered again.
  Set it in `compliance_file_load_config_var` (`"load_duplicate": "yes"`), or for one trigger of the
  file-load or the generic DAG with `{"load_duplicate": "yes"}`.
- There is no quarantine path and no archive path to configure. The subfolders are created when first
  needed and are never swept. Their names are the `ARCHIVE_FOLDER` / `ERROR_FOLDER` settings.
- A file moved into `Archive` / `Error` replaces one of the same name already there.
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
  emailed by the `NOTIFY` step to the file config's recipients.

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

**Tables.** `sql/schema.sql` defines 13 MULTISET tables for `meta_db`, created separately: the 12 framework tables plus
`ComplianceLock`. Identity columns are `GENERATED ALWAYS AS IDENTITY`; there are no foreign keys or
CHECK constraints (the validator checks references).

**Staging and core tables** are created by each project, in the databases named in
`ComplianceSourceFileConfig` (`Stg_Schema_Nm` / `Core_Schema_Nm` are Teradata database names):

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

With `RULE_ENGINE=gre` and `GRE_ENTRYPOINT=compliance_framework.gre_bridge:run_gre`, each
`ComplianceRuleBinding` of a file runs that GRE `rule_group` (and `rule_variant`; `*` = all) through the
GRE's `run_rules()`, with `run_key = CBF_LOAD_<Load_ID>` and the file's context as `run_params`
(`{btch_id}`, `{load_id}`, `{stg_schema_nm}`, `{stg_table_nm}`, `{core_schema_nm}`, `{core_table_nm}`, `{project_cd}`, `{table_nm}`, `{src_id}`,
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
  `teradatasql` driver backed by PostgreSQL, and the DAGs run end to end in Airflow 2.10.
- It has **not** been run against a real Teradata system. Before production, on a development Teradata
  system: create the tables from `sql/schema.sql`, run `validate-config`, then one full cycle (`BATCH_CREATION`, `FILE_LOAD` of a good
  and a bad file, `BATCH_CLOSE`, `NOTIFY`) with the step DAGs or `OSSTD_CORE_CMS_COMPLIANCE_RUN_MODULE`, and check the tables.
- The NAS store was checked against a local SMB test server for connect, list, read and download only,
  and with a stand-in for the rest. Moving files (`Archive` / `Error`), creating the subfolders and the
  modified-time check have **not** been run against your NAS: verify them with one good and one bad file.
- The ServiceNow callback and the email tasks ran with stand-ins for `snow` and the email backend.
