# Compliance Batch Framework: Airflow + Teradata

A self-contained copy of the compliance batch framework that runs from Airflow DAGs with its metadata,
staging and core tables in **Teradata**. Nothing outside `airflow/` is used or changed. It follows the
layout and connection style of the GRE rules engine's Airflow package (`DQ_COMPLETE_REPO/airflow`).

```
airflow/
  compliance_framework_dag.py     the only file outside the package: the DAGs
  compliance_framework/
    compliance_task.py            the only Airflow-aware module: Variable + Connections -> framework
    run_framework.py              run_step() / run_command(): the framework in-process, no Airflow
    connection_factory.py         Teradata connection (TERADATA_*) and NAS / SMB settings (NAS_*)
    db.py                         Teradata layer: transactions, lock table
    gre_bridge.py                 file rules through the GRE rules engine
    batches.py ingest.py load.py overrides.py closing.py audit.py config.py modules.py
    adapters.py app.py cli.py common.py settings.py
    sql/schema.sql                Teradata DDL ({{META_DB}} template)
    sql/approvals.sql             manual override templates
    examples/                     example Variables (prod, dev) and trigger configurations
    requirements.txt
```

## How it fits together

1. **One Airflow Variable** (`compliance_framework_config`, JSON) holds everything about the runs: the
   metadata database, the framework settings, and the named **runs** (project, steps, run type, period,
   schedule). Nothing about a run is hardcoded in the DAG file.
2. **Two Airflow Connections**, both named in the Variable: Teradata (`td_conn_var`) and the NAS file
   server (`wdc_comp_oper_nas_var`). The bridge exports them as `TERADATA_HOST` / `TERADATA_USER` /
   `TERADATA_PASSWORD` / `TERADATA_LOGMECH` and `NAS_HOST` / `NAS_USER` / `NAS_PASSWORD` / `NAS_PORT`.
3. A DAG task calls `compliance_task.run_compliance_step(step, ...)`, which merges, in this order: the
   Variable, the named run inside it, the trigger's configuration. It then runs one module in-process:
   `BATCH_CREATION`, `FILE_LOAD`, `OVERRIDE_DECISIONS`, `BATCH_CLOSE` or `NOTIFY`.
4. The task fails on a framework error (exit code 2), and on "completed with problems" (exit code 1)
   for the steps configured that way; otherwise the outcome is the task's XCom.

No `.env` file and no AWS Secrets Manager: credentials come from the Airflow Connections only.

## Deploying

1. Copy `airflow/compliance_framework_dag.py` and the `airflow/compliance_framework/` folder into the
   DAGs folder, side by side.
2. Install `compliance_framework/requirements.txt` in the workers' environment. Airflow 2.4+, Python 3.9+.
3. Create the two Connections and the Variable (below; ready-made examples in `examples/`).
4. The framework tables already exist: they are created separately from `sql/schema.sql` (replace
   `{{META_DB}}` with the metadata database). The framework never creates or alters tables.
5. Load the configuration tables, then trigger `COMPLIANCE_ADMIN` with `{"command": "validate-config"}`.

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

### Airflow Variable `compliance_framework_config`

Examples: [`examples/compliance_framework_config.prod.json`](examples/compliance_framework_config.prod.json),
[`examples/compliance_framework_config.dev.json`](examples/compliance_framework_config.dev.json). Paste one
as the Variable's value (Admin → Variables) and replace the `REPLACE_WITH_...` values.

```json
{
  "td_conn_var": "compliance_teradata",
  "wdc_comp_oper_nas_var": "wdc_comp_oper_nas",
  "meta_db": "CMS_COMPLIANCE_PROD",
  "load_env": "PROD",
  "email_recipient": "compliance-ops@example.com",
  "schedule_timezone": "America/Chicago",
  "settings": {"NOTIFY_BACKEND": "airflow", "DEFAULT_NOTIFY_EMAILS": "compliance-ops@example.com"},
  "runs": {
    "odr_daily_batches": {"schedule_interval": "0 6 * * *", "project": "ODR", "steps": ["BATCH_CREATION"],
                          "run_type": "DAILY", "period": "PREV_DAY", "email_recipient": "odr-team@example.com"},
    "odr_file_load":     {"schedule_interval": "*/15 * * * *", "project": "ODR",
                          "steps": ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"]},
    "odr_full_cycle":    {"project": "ODR", "run_type": "DAILY", "period": "PREV_DAY",
                          "steps": ["BATCH_CREATION", "FILE_LOAD", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY"]}
  }
}
```

| Key | Meaning |
|---|---|
| `meta_db` (required) | Teradata database holding the framework tables |
| `td_conn_var` | Airflow Connection of Teradata. Falls back to `connection_id`, then `gre.connection_id`, then `compliance_teradata` |
| `wdc_comp_oper_nas_var` (required for NAS) | Airflow Connection of the NAS file server |
| `load_env` | `DEV`, `TEST`, `QA`, `PROD`, ...: shown in the email subjects, and what `$env` in configured database names and paths resolves to (below); falls back to `environment`, then `gre.environment`, else `DEV` |
| `load_duplicate` | `yes` / `no` (default `no`): load a file whose name was already loaded, or reject it. A run and a trigger can set their own |
| `email_recipient` | Who gets the DAG success / failure emails (`success_email`, `fail_email` tasks); a run can set its own. Without it the DAGs have no email tasks |
| `snow_assignment_group` | ServiceNow assignment group of the incident opened when a task fails (default `D&AE - EDE Govt Compliance`) |
| `owner`, `tags` | DAG owner (default `oss`) and tags |
| `schedule_timezone` | Time zone of the schedules (default `America/Chicago`; follows daylight saving) |
| `settings` | Framework settings by name. Common: `ARCHIVE_FOLDER` (`Archive`), `ERROR_FOLDER` (`Error`), `NAS_MIN_AGE_SECONDS` (120), `BUSINESS_TZ`, `NOTIFY_BACKEND` (`airflow` = Airflow's email backend, `ses`, `log`), `NOTIFY_FROM_EMAIL` (ses), `DEFAULT_NOTIFY_EMAILS`, `FILE_RULES_MODE` (`GATE` / `ANNOTATE`), `RULE_ENGINE` (`gre` / `none`), `GRE_ENTRYPOINT`, `LOCK_TIMEOUT_SECONDS` (300), `LOCK_TTL_MINUTES` (240) |
| `runs` | Named parameter sets, below |
| `gre` | For `gre_bridge`. It takes the GRE's own Airflow Variable as it is: `connection_type` (`teradata`), `connection_id`, `environment`, `meta_db` (GRE metadata database), `meta_connection`, `project_name`, `run_params`, `text_params`, `extra_filters`, `log_level`, `max_parallel_rules`; plus `package_dir` (folder holding the GRE's `run_rules.py`; default `<dags>/rules_engine`) |
| `fail_on_problems` | Per step, whether exit code 1 fails the task. Default: `BATCH_CREATION`, `BATCH_CLOSE`, `NOTIFY` true; `FILE_LOAD`, `OVERRIDE_DECISIONS` false (a bad file or override is audited and emailed, not a task failure) |
| `log_level` | `INFO` (default), `DEBUG`, ... |

Each entry of `runs` (name: letters, digits, `_`):

| Key | Meaning |
|---|---|
| `steps` | Which steps, any of `BATCH_CREATION`, `FILE_LOAD`, `OVERRIDE_DECISIONS`, `BATCH_CLOSE`, `NOTIFY`; they always run in that order |
| `project` | `Project_Cd`; leave out to run the steps for every project |
| `run_type`, `period`, `table`, `lookback_days`, `lookback_weeks` | Passed to the steps that take them (`period`: `PREV_DAY`, `PREV_CALENDAR_MONTH`, `CURRENT_CALENDAR_MONTH`, `PREV_CALENDAR_WEEK`, ...) |
| `schedule_interval` | A cron expression. A run **with** one becomes its own DAG; one without is only run by hand |
| `dag_id` | Name of that DAG (default `COMPLIANCE_<RUN NAME>`, upper case) |
| `email_recipient`, `tags` | For that DAG, instead of / in addition to the Variable's |
| `settings`, `fail_on_problems` | Overrides of the Variable's, for this run only |

## Files on the NAS

Files are read from the NAS over SMB (`OBJECT_STORE` = `nas`, the default; library `smbprotocol`).

**Inbound folder** — `ComplianceSourceFileConfig.S3_Src_File_Path` (the column keeps its name) holds the folder:

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
| Not loaded: unknown name, no batch or a closed batch, wrong column count, bad characters, an empty file where one is not allowed, failed its file rules, a name already loaded (unless `load_duplicate` = `yes`) | `<inbound folder>\Error\` |
| Technical failure | stays in place and is retried by the next sweep |

- **Duplicate file names (`LOAD_DUPLICATE`).** A file whose name was already loaded is rejected by
  default (`FILE_REJECTED_DUPLICATE`, moved to `Error`). With `LOAD_DUPLICATE` = `yes` it is loaded like
  any other file: while the batch is open it replaces the batch's data; a closed batch still needs the
  usual override. A name whose earlier delivery was rejected can always be delivered again.
  Set it in the Variable (`"load_duplicate": "yes"` at the top or inside one run), or for one trigger of
  `COMPLIANCE_BATCH_FRAMEWORK` with the `load_duplicate` parameter / `{"load_duplicate": "yes"}`.
- There is no quarantine path and no archive path to configure. The subfolders are created when first
  needed and are never swept. Their names are the `ARCHIVE_FOLDER` / `ERROR_FOLDER` settings.
- A file moved into `Archive` / `Error` replaces one of the same name already there.
- **Files still being copied:** a file changed in the last `NAS_MIN_AGE_SECONDS` (default 120) is left
  for the next sweep, so a half-written file is not loaded. The check compares the file's modified time
  on the NAS with the worker's clock.
- A file is identified by its path, size and modified time: delivering the same name again is a new
  load; a sweep that sees an untouched file again (one whose move failed) does not reload it.
- `ComplianceFileLoad.S3_Bucket` holds `<server>/<share>` and `S3_Key` the path inside the share; audit
  rows show the file as `//server/share/path`.
- `OBJECT_STORE` = `s3` (needs `boto3`) and `local` still exist; the Archive / Error rule is the same.

## Failure tickets and emails

- Every task has `on_failure_callback`: a failed task opens a ServiceNow incident through
  `snow.snow_integrations.create_incident(context, assignment_group)`. If the `snow` package is not
  installed on the workers, a warning is logged instead.
- With `email_recipient` set, each DAG ends with `success_email` (no step failed) and `fail_email` (a
  step failed), subjects `Airflow EDEG-Compliance <load_env> Success|Failure: <dag id> DAG`. They use
  Airflow's own email settings.
- These are about the DAG run. Business events (a rejected file, a missing source, an override) are
  emailed by the `NOTIFY` step to the file config's recipients.

## DAGs

| DAG | What it does |
|---|---|
| `COMPLIANCE_BATCH_FRAMEWORK` | **The one DAG that calls every step**, in order, by hand: for a named run (`{"run": "odr_full_cycle"}`) or for parameters given at trigger time. Steps not in the run are skipped. |
| `COMPLIANCE_<RUN NAME>` (or the run's `dag_id`) | One per run that has a `schedule_interval`, e.g. `COMPLIANCE_ODR_FILE_LOAD`. Built from the Variable when Airflow parses the DAG file; `max_active_runs=1`. |
| `COMPLIANCE_ADMIN` | `validate-config`, `health`, `locks`, `release-lock`, `close-batch`, ... |

- Change a run's parameters, add a project or add a schedule by editing the Variable; a new or changed
  schedule appears at Airflow's next parse of the DAG file. A missing or invalid Variable never breaks
  parsing: the scheduled DAGs are simply not created (see the scheduler log), the other two remain.
- Steps run in order; a failed step stops the rest and fails the run. Tasks do not retry (`retries: 0`);
  every step is idempotent, so the next scheduled run or a manual re-run picks up. Overlapping DAGs are
  safe (locks, below).
- A trigger's configuration overrides the run and the Variable. Examples for every case are in
  [`examples/trigger_configurations.json`](examples/trigger_configurations.json):
  - a named run: `{"run": "odr_full_cycle"}`; the same for a missed day: `{"run": "odr_full_cycle", "as_of": "2026-09-01"}`
  - parameters given directly: `{"project": "UNIVERSE", "steps": ["BATCH_CREATION", "FILE_LOAD"], "run_type": "MONTHLY", "period": "PREV_CALENDAR_MONTH"}`
  - a setting for one run: `{"project": "ODR", "steps": ["FILE_LOAD"], "settings": {"FILE_RULES_MODE": "ANNOTATE"}}`
  - another environment's Variable: `{"variable_key": "compliance_framework_config_qa", "run": "odr_full_cycle"}`
  - `COMPLIANCE_ADMIN`: `{"command": "close-batch", "args": ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]}`
- The scheduled DAGs read the Variable named by the `COMPLIANCE_VARIABLE_KEY` environment variable
  (default `compliance_framework_config`).

## Environment in database names (`$env`)

The staging and core database names and the inbound path of `ComplianceSourceFileConfig` can carry
a `$env` token, so one set of configuration rows works in every environment (the GRE convention):

| Authored | DEV | TEST | QA | PROD / UAT |
|---|---|---|---|---|
| `CMS_$ENV_STG` | `CMS_DEV_STG` | `CMS_TEST_STG` | `CMS_QA_STG` | `CMS_STG` |
| `cms_core_$env_t` | `cms_core_dev_t` | `cms_core_test_t` | `cms_core_qa_t` | `cms_core_t` |
| `//nas01/Compliance/$env/odr/in` | `//nas01/Compliance/dev/odr/in` | `//nas01/Compliance/test/odr/in` | `//nas01/Compliance/qa/odr/in` | `//nas01/Compliance/odr/in` |

- Columns: `Stg_Schema_Nm`, `Core_Schema_Nm`, `S3_Src_File_Path`.
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

Reads use `LOCKING ROW FOR ACCESS`; every change to a batch is made under that batch's lock row.
String comparisons follow the session's transaction mode (Teradata mode is not case specific).

## File rules through the GRE

With `RULE_ENGINE=gre` and `GRE_ENTRYPOINT=compliance_framework.gre_bridge:run_gre`, each
`ComplianceRuleBinding` of a file runs that GRE `rule_group` (and `rule_variant`; `*` = all) through the
GRE's `run_rules()`, with `run_key = CBF_LOAD_<Load_ID>` and the file's context as `run_params`
(`{btch_id}`, `{load_id}`, `{stg_schema_nm}`, `{stg_table_nm}`, `{project_cd}`, `{table_nm}`, `{src_id}`,
`{run_ty}`, `{rpt_start_dt_key}`, `{rpt_end_dt_key}`, `{req_dt_key}`). The verdict of each rule is read
from `<gre meta_db>.gre_results`: `PASS` / `WARN` pass, `FAIL` fails the file (GATE) or annotates it
(ANNOTATE), anything else is a technical failure that is retried. Use `RULE_ENGINE=none` to load without
rules.

The Variable's `gre` section is passed on to `run_rules()`: `project_name`, `text_params` and
`extra_filters` as written, and `run_params` merged with the file's context (the file's values win).
`text_params` and `extra_filters` are the same for every file of the Variable: where they name a run type
(e.g. `{"RUNTYPE": "MNT"}`, `{"run_ty": "MNT"}`), give a run that loads another run type its own values
under that run's `gre` key. The rules run on the framework's Teradata connection.

## Validation status

- The framework logic (batch creation, intake, file load, promotion, overrides, close, notifications,
  locks) and the GRE bridge pass the framework's test suite when run against a stand-in for the
  `teradatasql` driver backed by PostgreSQL, and the DAGs run end to end in Airflow 2.10.
- It has **not** been run against a real Teradata system. Before production, on a development Teradata
  system: create the tables from `sql/schema.sql`, run `validate-config`, then one full cycle (`BATCH_CREATION`, `FILE_LOAD` of a good
  and a bad file, `BATCH_CLOSE`, `NOTIFY`) from `COMPLIANCE_BATCH_FRAMEWORK`, and check the tables.
- The NAS store was checked against a local SMB test server for connect, list, read and download only,
  and with a stand-in for the rest. Moving files (`Archive` / `Error`), creating the subfolders and the
  modified-time check have **not** been run against your NAS: verify them with one good and one bad file.
- The ServiceNow callback and the email tasks ran with stand-ins for `snow` and the email backend.
