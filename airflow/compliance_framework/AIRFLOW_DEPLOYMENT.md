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
    db.py                         Teradata layer: transactions, lock table, init-db
    gre_bridge.py                 file rules through the GRE rules engine
    batches.py ingest.py load.py overrides.py closing.py audit.py config.py modules.py
    adapters.py app.py cli.py common.py settings.py
    sql/schema.sql                Teradata DDL ({{META_DB}} template)
    sql/approvals.sql             manual override templates
    requirements.txt
```

## How it fits together

1. A DAG task calls `compliance_task.run_compliance_step(step, ...)`.
2. The bridge reads one **Airflow Variable** (JSON: metadata database, framework settings) and merges the
   trigger's configuration over it, reads the Teradata credentials from one **Airflow Connection**, and
   exports `TERADATA_HOST` / `TERADATA_USER` / `TERADATA_PASSWORD` / `TERADATA_LOGMECH`.
3. `run_framework.run_step()` opens the Teradata connection (`connection_factory.py`) with the metadata
   database as default database and runs one module: `BATCH_CREATION`, `FILE_LOAD`,
   `OVERRIDE_DECISIONS`, `BATCH_CLOSE` or `NOTIFY`.
4. The task fails on a framework error (exit code 2), and on "completed with problems" (exit code 1)
   for the steps configured that way; otherwise the outcome is returned as the task's XCom.

No `.env` file and no AWS Secrets Manager: credentials come from the Airflow Connection only.

## Setup

### 1. Install

Copy `airflow/` into the DAGs folder (keep `compliance_framework/` next to
`compliance_framework_dag.py`) and install `compliance_framework/requirements.txt` in the workers'
environment. Airflow 2.4+ (uses `schedule=`), Python 3.9+.

### 2. Airflow Connection

| Field | Value |
|---|---|
| Connection Id | `compliance_teradata` (or name it in the Variable's `connection_id`) |
| Connection Type | Teradata, or Generic |
| Host / Login / Password | the Teradata system and the framework's service account |
| Extra (optional) | `{"logmech": "LDAP"}` (default LDAP) |

The account needs, on the metadata database: CREATE TABLE (for `init-db`) and SELECT / INSERT / UPDATE /
DELETE; on each project's staging and core databases: SELECT / INSERT / UPDATE / DELETE; and SELECT on
`DBC.TablesV` and `DBC.ColumnsV`.

### 3. Airflow Variable `compliance_framework_config`

```json
{
  "connection_id": "compliance_teradata",
  "meta_db": "CMS_COMPLIANCE_PROD",
  "log_level": "INFO",
  "settings": {
    "QUARANTINE_URI": "s3://my-inbound-bucket/quarantine/",
    "BUSINESS_TZ": "America/Chicago",
    "NOTIFY_BACKEND": "airflow",
    "DEFAULT_NOTIFY_EMAILS": "compliance-ops@example.com",
    "RULE_ENGINE": "gre",
    "GRE_ENTRYPOINT": "compliance_framework.gre_bridge:run_gre"
  },
  "gre": {"environment": "PROD", "meta_db": "GRE_META_PROD"}
}
```

| Key | Meaning |
|---|---|
| `meta_db` (required) | Teradata database holding the framework tables |
| `connection_id` | Airflow Connection (default `compliance_teradata`) |
| `settings` | Framework settings by name. Common: `QUARANTINE_URI`, `BUSINESS_TZ`, `AWS_REGION`, `NOTIFY_BACKEND` (`airflow` = Airflow's email backend, `ses`, `log`), `NOTIFY_FROM_EMAIL` (ses), `DEFAULT_NOTIFY_EMAILS`, `FILE_RULES_MODE` (`GATE` / `ANNOTATE`), `RULE_ENGINE` (`gre` / `none`), `GRE_ENTRYPOINT`, `LOCK_TIMEOUT_SECONDS` (300), `LOCK_TTL_MINUTES` (240) |
| `gre` | For `gre_bridge`: `meta_db` (GRE metadata database), `environment`, `package_dir` (folder holding the GRE's `run_rules.py`; default `<dags>/rules_engine`), `log_level` |
| `fail_on_problems` | Per step, whether exit code 1 fails the task. Default: `BATCH_CREATION`, `BATCH_CLOSE`, `NOTIFY` true; `FILE_LOAD`, `OVERRIDE_DECISIONS` false (a bad file or override is audited and emailed, not a task failure) |
| `log_level` | `INFO` (default), `DEBUG`, ... |

Files are read from S3 with the workers' AWS credentials (`OBJECT_STORE` = `s3`, the default).

### 4. First run

Trigger **`compliance_admin`** with configuration:

1. `{"command": "init-db"}` creates the tables in `meta_db` (skipped when they exist).
2. Insert the configuration rows (`ComplianceProject`, `ComplianceSourceSystem`, `ComplianceRunType`,
   `ComplianceDataSetSourceXwalk`, `ComplianceSourceFileConfig`, `ComplianceRuleBinding`) and create each
   project's staging and core tables (below).
3. `{"command": "validate-config"}` must succeed.

Then set the real projects, run types and times in `SCHEDULES` at the top of
`compliance_framework_dag.py` and unpause the DAGs.

## DAGs

| DAG | Schedule (America/Chicago, follows daylight saving) | Steps |
|---|---|---|
| `compliance_<project>_daily_batches` | 06:00 | `BATCH_CREATION` for the DAG's `run_type` / `period` |
| `compliance_<project>_file_load` | every 15 minutes | `FILE_LOAD` → `OVERRIDE_DECISIONS` → `NOTIFY` |
| `compliance_<project>_close` | hourly / 23:30 | `BATCH_CLOSE` → `NOTIFY` |
| `compliance_all_projects_notify` | hourly | `NOTIFY` for events that belong to no project |
| `compliance_manual_run` | manual | any steps for one project |
| `compliance_admin` | manual | `init-db`, `validate-config`, `health`, `locks`, `release-lock`, `close-batch`, ... |

- One DAG per entry of `SCHEDULES`; add a project by adding its entries. `max_active_runs=1` keeps a
  schedule from overlapping itself; overlapping DAGs are safe (locks, below).
- Steps run in order; a failed step stops the rest and fails the run (`alert_emails` of the entry get
  Airflow's failure email). Tasks retry once after 5 minutes: every step is idempotent.
- **Manual run / re-run a missed day** — trigger with configuration; it overrides the DAG and the Variable:
  - `compliance_manual_run`: `{"project": "ODR", "steps": ["BATCH_CREATION", "FILE_LOAD"], "run_type": "DAILY", "period": "PREV_DAY", "as_of": "2026-09-01"}`
  - a scheduled DAG: `{"as_of": "2026-09-01"}`, or `{"variable_key": "compliance_framework_config_qa"}`
- `compliance_admin` examples: `{"command": "close-batch", "args": ["--btch-id", "<Btch_ID>", "--closed-by", "jdoe"]}`,
  `{"command": "release-lock", "args": ["--key", "BTCH:<Btch_ID>"]}`.

## Teradata specifics

**Tables.** `sql/schema.sql` creates 13 MULTISET tables in `meta_db`: the 12 framework tables plus
`ComplianceLock`. Identity columns are `GENERATED ALWAYS AS IDENTITY`; there are no foreign keys or
CHECK constraints (the validator checks references).

**Staging and core tables** are created by each project, in the databases named in
`ComplianceSourceFileConfig` (`Stg_Schema_Nm` / `Core_Schema_Nm` are Teradata database names):

- Staging: the business columns in file order, then `btch_id VARCHAR(250)`, `load_id BIGINT`,
  `src_file_nm VARCHAR(1024)`, `stg_load_dtts TIMESTAMP(6) WITH TIME ZONE`. File values arrive as
  text: declare business columns VARCHAR, or types Teradata converts from text implicitly (a value that
  does not convert quarantines the file as `FILE_PARSE_ERROR`).
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
  system: run `init-db`, `validate-config`, then one full cycle (`BATCH_CREATION`, `FILE_LOAD` of a good
  and a bad file, `BATCH_CLOSE`, `NOTIFY`) from `compliance_manual_run`, and check the tables.
