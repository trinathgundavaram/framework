# Compliance Batch Framework: Airflow + Teradata

A self-contained copy of the compliance batch framework that runs from Airflow DAGs with its metadata,
staging and core tables in **Teradata**. Nothing outside `airflow/` is used or changed. It follows the
layout and connection style of the GRE rules engine's Airflow package (`DQ_COMPLETE_REPO/airflow`).

```
airflow/
  compliance_framework_dag.py     the only file outside the package: the DAGs
  compliance_framework/
    compliance_task.py            the only Airflow-aware module: Variable + Connection -> framework
    run_framework.py              run_step() / run_command(): the framework in-process, no Airflow
    connection_factory.py         teradatasql connection from TERADATA_* environment variables
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
2. **One Airflow Connection** (`compliance_teradata`) holds the Teradata host, login and password. The
   bridge exports them as `TERADATA_HOST` / `TERADATA_USER` / `TERADATA_PASSWORD` / `TERADATA_LOGMECH`.
3. A DAG task calls `compliance_task.run_compliance_step(step, ...)`, which merges, in this order: the
   Variable, the named run inside it, the trigger's configuration. It then runs one module in-process:
   `BATCH_CREATION`, `FILE_LOAD`, `OVERRIDE_DECISIONS`, `BATCH_CLOSE` or `NOTIFY`.
4. The task fails on a framework error (exit code 2), and on "completed with problems" (exit code 1)
   for the steps configured that way; otherwise the outcome is the task's XCom.

No `.env` file and no AWS Secrets Manager: credentials come from the Airflow Connection only.

## Deploying

1. Copy `airflow/compliance_framework_dag.py` and the `airflow/compliance_framework/` folder into the
   DAGs folder, side by side.
2. Install `compliance_framework/requirements.txt` in the workers' environment. Airflow 2.4+, Python 3.9+.
3. Create the Connection and the Variable (below; ready-made examples in `examples/`).
4. The framework tables already exist: they are created separately from `sql/schema.sql` (replace
   `{{META_DB}}` with the metadata database). The framework never creates or alters tables.
5. Load the configuration tables, then trigger `compliance_admin` with `{"command": "validate-config"}`.

### Airflow Connection

| Field | Value |
|---|---|
| Connection Id | `compliance_teradata` (or name it in the Variable's `connection_id`) |
| Connection Type | Teradata, or Generic |
| Host / Login / Password | the Teradata system and the framework's service account |
| Extra (optional) | `{"logmech": "LDAP"}` (default LDAP) |

The account needs, on the metadata database: SELECT / INSERT / UPDATE / DELETE (no CREATE); on each project's staging and core databases: SELECT / INSERT / UPDATE / DELETE; and SELECT on
`DBC.TablesV` and `DBC.ColumnsV`.

### Airflow Variable `compliance_framework_config`

Examples: [`examples/compliance_framework_config.prod.json`](examples/compliance_framework_config.prod.json),
[`examples/compliance_framework_config.dev.json`](examples/compliance_framework_config.dev.json). Paste one
as the Variable's value (Admin → Variables) and replace the `REPLACE_WITH_...` values.

```json
{
  "connection_id": "compliance_teradata",
  "meta_db": "CMS_COMPLIANCE_PROD",
  "schedule_timezone": "America/Chicago",
  "settings": {"QUARANTINE_URI": "s3://my-inbound-bucket/quarantine/", "NOTIFY_BACKEND": "airflow",
               "DEFAULT_NOTIFY_EMAILS": "compliance-ops@example.com"},
  "runs": {
    "odr_daily_batches": {"schedule": "0 6 * * *", "project": "ODR", "steps": ["BATCH_CREATION"],
                          "run_type": "DAILY", "period": "PREV_DAY", "alert_emails": ["odr-team@example.com"]},
    "odr_file_load":     {"schedule": "*/15 * * * *", "project": "ODR",
                          "steps": ["FILE_LOAD", "OVERRIDE_DECISIONS", "NOTIFY"]},
    "odr_full_cycle":    {"project": "ODR", "run_type": "DAILY", "period": "PREV_DAY",
                          "steps": ["BATCH_CREATION", "FILE_LOAD", "OVERRIDE_DECISIONS", "BATCH_CLOSE", "NOTIFY"]}
  }
}
```

| Key | Meaning |
|---|---|
| `meta_db` (required) | Teradata database holding the framework tables |
| `connection_id` | Airflow Connection (default `compliance_teradata`) |
| `environment` | `DEV`, `TEST`, `QA`, `PROD`, ...: what `$env` in configured database names resolves to (below); defaults to `gre.environment`, else `DEV` |
| `schedule_timezone` | Time zone of the schedules (default `America/Chicago`; follows daylight saving) |
| `settings` | Framework settings by name. Common: `QUARANTINE_URI`, `BUSINESS_TZ`, `AWS_REGION`, `NOTIFY_BACKEND` (`airflow` = Airflow's email backend, `ses`, `log`), `NOTIFY_FROM_EMAIL` (ses), `DEFAULT_NOTIFY_EMAILS`, `FILE_RULES_MODE` (`GATE` / `ANNOTATE`), `RULE_ENGINE` (`gre` / `none`), `GRE_ENTRYPOINT`, `LOCK_TIMEOUT_SECONDS` (300), `LOCK_TTL_MINUTES` (240) |
| `runs` | Named parameter sets, below |
| `gre` | For `gre_bridge`: `meta_db` (GRE metadata database), `environment`, `package_dir` (folder holding the GRE's `run_rules.py`; default `<dags>/rules_engine`), `log_level` |
| `fail_on_problems` | Per step, whether exit code 1 fails the task. Default: `BATCH_CREATION`, `BATCH_CLOSE`, `NOTIFY` true; `FILE_LOAD`, `OVERRIDE_DECISIONS` false (a bad file or override is audited and emailed, not a task failure) |
| `log_level` | `INFO` (default), `DEBUG`, ... |

Each entry of `runs` (name: letters, digits, `_`):

| Key | Meaning |
|---|---|
| `steps` | Which steps, any of `BATCH_CREATION`, `FILE_LOAD`, `OVERRIDE_DECISIONS`, `BATCH_CLOSE`, `NOTIFY`; they always run in that order |
| `project` | `Project_Cd`; leave out to run the steps for every project |
| `run_type`, `period`, `table`, `lookback_days`, `lookback_weeks` | Passed to the steps that take them (`period`: `PREV_DAY`, `PREV_CALENDAR_MONTH`, `CURRENT_CALENDAR_MONTH`, `PREV_CALENDAR_WEEK`, ...) |
| `schedule` | A cron expression. A run **with** a schedule becomes its own DAG `compliance_<run name>`; one without is only run by hand |
| `alert_emails` | Who gets Airflow's failure email for that DAG |
| `settings`, `fail_on_problems` | Overrides of the Variable's, for this run only |

Files are read from S3 with the workers' AWS credentials (`OBJECT_STORE` = `s3`, the default).

## DAGs

| DAG | What it does |
|---|---|
| `compliance_batch_framework` | **The one DAG that calls every step**, in order, by hand: for a named run (`{"run": "odr_full_cycle"}`) or for parameters given at trigger time. Steps not in the run are skipped. |
| `compliance_<run name>` | One per run that has a `schedule`, e.g. `compliance_odr_file_load`. Built from the Variable when Airflow parses the DAG file; `max_active_runs=1`. |
| `compliance_admin` | `validate-config`, `health`, `locks`, `release-lock`, `close-batch`, ... |

- Change a run's parameters, add a project or add a schedule by editing the Variable; a new or changed
  schedule appears at Airflow's next parse of the DAG file. A missing or invalid Variable never breaks
  parsing: the scheduled DAGs are simply not created (see the scheduler log), the other two remain.
- Steps run in order; a failed step stops the rest and fails the run. Tasks retry once after 5 minutes:
  every step is idempotent. Overlapping DAGs are safe (locks, below).
- A trigger's configuration overrides the run and the Variable. Examples for every case are in
  [`examples/trigger_configurations.json`](examples/trigger_configurations.json):
  - a named run: `{"run": "odr_full_cycle"}`; the same for a missed day: `{"run": "odr_full_cycle", "as_of": "2026-09-01"}`
  - parameters given directly: `{"project": "UNIVERSE", "steps": ["BATCH_CREATION", "FILE_LOAD"], "run_type": "MONTHLY", "period": "PREV_CALENDAR_MONTH"}`
  - a setting for one run: `{"project": "ODR", "steps": ["FILE_LOAD"], "settings": {"FILE_RULES_MODE": "ANNOTATE"}}`
  - another environment's Variable: `{"variable_key": "compliance_framework_config_qa", "run": "odr_full_cycle"}`
  - `compliance_admin`: `{"command": "close-batch", "args": ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]}`
- The scheduled DAGs read the Variable named by the `COMPLIANCE_VARIABLE_KEY` environment variable
  (default `compliance_framework_config`).

## Environment in database names (`$env`)

The staging and core database names and the S3 paths of `ComplianceSourceFileConfig` can carry
a `$env` token, so one set of configuration rows works in every environment (the GRE convention):

| Authored | DEV | TEST | QA | PROD / UAT |
|---|---|---|---|---|
| `CMS_$ENV_STG` | `CMS_DEV_STG` | `CMS_TEST_STG` | `CMS_QA_STG` | `CMS_STG` |
| `cms_core_$env_t` | `cms_core_dev_t` | `cms_core_test_t` | `cms_core_qa_t` | `cms_core_t` |
| `s3://inbound-$env/odr/in/` | `s3://inbound-dev/odr/in/` | `s3://inbound-test/odr/in/` | `s3://inbound-qa/odr/in/` | `s3://inbound-/odr/in/` |

- Columns: `Stg_Schema_Nm`, `Core_Schema_Nm`, `S3_Src_File_Path`, `Src_File_Archive_Path`.
- The token matches in any casing and is replaced in its own casing (`$env` → `dev`, `$ENV` → `DEV`,
  `$Env` → `Dev`). PROD and UAT replace it with nothing; a doubled underscore left in a database name
  collapses (paths are left as they are, so prefer a database-style name where PROD has no suffix).
- Keep the token in the middle of a database name: at the very end or start it leaves the underscore
  behind in PROD and UAT (`CMS_STG_$ENV` → `CMS_STG_`), exactly as GRE does.
- The environment is the `ENVIRONMENT` setting (the Variable's `environment`); `ENV_VALUE` overrides the replacement text for
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

## Validation status

- The framework logic (batch creation, intake, file load, promotion, overrides, close, notifications,
  locks) and the GRE bridge pass the framework's test suite when run against a stand-in for the
  `teradatasql` driver backed by PostgreSQL, and the DAGs run end to end in Airflow 2.10.
- It has **not** been run against a real Teradata system. Before production, on a development Teradata
  system: create the tables from `sql/schema.sql`, run `validate-config`, then one full cycle (`BATCH_CREATION`, `FILE_LOAD` of a good
  and a bad file, `BATCH_CLOSE`, `NOTIFY`) from `compliance_batch_framework`, and check the tables.
