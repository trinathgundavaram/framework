# CMS Compliance Framework: Python Package

This is the implementation of [`docs/design/cms-compliance-framework-design.md`](design/cms-compliance-framework-design.md) (v3.2).
- **Project-agnostic:** every project, table, source and run type is configuration. The code has no project-specific branches.
- **Filename-driven:** incoming files are recognised by the templates stored in `ComplianceSourceFileConfig`.
- **Metadata-driven:** runtime settings and data-database connections live in metadata tables; the environment and an optional `framework.ini` can override them (see [Configuration](#configuration)).
- **Phase 1 scope (design §3):** a local, orchestration-agnostic package with a CLI. Glue or Step Functions wrappers come later (D-13).

## Layout

```
pyproject.toml                  package metadata; `framework` console script
framework.ini.example           sample config file (copy to framework.ini)
docker-compose.yml              local PostgreSQL 16 for development/tests
src/framework/
  cli.py                        entry points (one command = one service)
  app.py                        service wiring + start-up (settings -> metadata connection -> metadata settings)
  settings.py                   runtime settings (env > config file > ComplianceFrameworkSetting > default)
  connections.py                metadata + named data connections (env > config file > metadata row > secret)
  clock.py, db.py, locks.py, errors.py, health.py
  config/                       models, repository, filename templates (§9), validator (§5.1)
  common/                       Btch_ID + SLA hold, Req_Stat model (abstract states, §6.1)
  audit/event_logger.py         the only writer of the two audit tables
  batches/                      scheduler + catch-up (P2/P3), intake (P4), period strategies, CRC repository
  ingest/                       pipeline (P5, C0-C14), resolution decision tables (§8), file reader
  load/                         staging engines (pandas/COPY, Spark), promotion core swap (§10.2), archive re-stage
  overrides/decision_processor  manual SQL approvals / rejections / waivers (P7) + reopen promotion
  validation/gre_adapter.py     UMcM GRE adapter (Q-12)
  extract/                      eligibility (§11.2), refresh/combine (P9), trigger + close (§11.3), evaluator (P10), connectors
  notify/notifier.py            SES / SNS / log notifications
  storage/object_store.py       S3 and local object stores
  sql/ddl/001_schema.sql        schema - source of truth (mirrors design Appendix A)
  sql/seed/*.sql                event types, period strategies, PROVISIONAL Req_Stat values (Q-01)
  sql/period_strategies/*.sql   report-period calculations
  sql/templates/approvals.sql   manual approval / waiver SQL (design Appendix B)
tests/                          119 tests (unit + PostgreSQL integration; single or split databases)
```

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d
export TEST_DATABASE_URL=postgresql://framework:framework@localhost:5432/framework
pytest                                 # tests drop/recreate the metadata schema - use a scratch database

cp framework.ini.example framework.ini # then edit [metadata_db] (or export FRAMEWORK_DB_DSN=...)
framework init-db                      # metadata schema + DDL (skipped if present) + seeds + default settings
framework test-connections
framework show-config
framework validate-config
```

Optional test modes:
- `TEST_METADATA_SCHEMA=fw_meta` runs the suite against a non-default metadata schema.
- `TEST_DATA_DATABASE_URL=postgresql://.../fwdata` puts the staging/core tables in a second database, registered as connection `DATA1` (split-database mode).

Python 3.10+ (tested on 3.10 and 3.11) and PostgreSQL 14+ with `btree_gist` (tested on 16).

### Windows without Docker

1. **Install Python 3.10+** from python.org, and tick "Add python.exe to PATH".
2. **Install PostgreSQL 14+.**
   - With admin rights: use the EDB installer (<https://www.postgresql.org/download/windows/>). It includes `btree_gist`.
   - Without admin rights: download the EDB **zip binaries**, unzip them to e.g. `C:\pgsql`, and run:
     ```powershell
     C:\pgsql\bin\initdb.exe -D C:\pgdata -U postgres -A trust -E UTF8
     C:\pgsql\bin\pg_ctl.exe -D C:\pgdata -l C:\pgdata\log.txt start
     C:\pgsql\bin\createdb.exe -U postgres fwtest
     C:\pgsql\bin\createdb.exe -U postgres fwdata     # optional: split-database tests
     ```
3. **Set up the environment and run the tests** (PowerShell):
   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   pip install -e ".[dev]"
   $env:TEST_DATABASE_URL = "postgresql://postgres@localhost:5432/fwtest"
   pytest
   $env:TEST_DATA_DATABASE_URL = "postgresql://postgres@localhost:5432/fwdata"   # optional
   pytest
   ```
   To run the CLI, copy `framework.ini.example` to `framework.ini`, set `[metadata_db]` and `[settings] object_store = local`, then `framework init-db`.
4. **Stop the database** when you're done: `C:\pgsql\bin\pg_ctl.exe -D C:\pgdata stop`.

## Onboarding a project (config only)

1. **Register the data database** (skip if the tables live in the metadata database):
   ```sql
   INSERT INTO ComplianceDbConnection (Connection_Nm, Connection_Desc, Host, Port, Database_Nm, User_Nm, Sslmode, Secret_Nm)
   VALUES ('PRJA_DW', 'Project A warehouse', 'prja-db.example.internal', 5432, 'prja', 'framework_app', 'require', 'prja/dw/framework');
   ```
   Then run `framework test-connections`.
2. **Create the target tables** in that database.
   - Staging: business columns in file order, plus `btch_id`, `load_id`, `src_file_nm`, `stg_load_dtts`.
   - Core: business columns plus `btch_id`, `load_id`, `current_ind`, `load_dtts`, `end_dtts`.
   - See the DDL comment at the end of `001_schema.sql`.
3. **Insert config rows**, in this order:
   1. `ComplianceSourceSystem`
   2. `ComplianceRunType` (`SLA_Days` ≥ 1)
   3. `ComplianceDataSetSourceXwalk` (one row per source × run type, effective-dated; ROUTINE rows need a cron and a period strategy)
   4. `ComplianceSourceFileConfig` (one active row per project/table/source: template, aliases, file format, engine, GATE/ANNOTATE, S3 paths, `Target_Connection_Nm`, targets; all sources of a table use the same connection)
   5. `ComplianceExtractPolicy` + `ComplianceExtractJobParam` (one per project/table/run type)
   6. `ComplianceRuleBinding`
4. **Run `framework validate-config`.** It must report no `ERROR` issues. Target tables are checked in the target connection's database.

Filename template example (literal text plus exactly one of each placeholder, separated by literals):

```
{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt
```

## Commands

| Command | Purpose | Typical trigger |
|---|---|---|
| `init-db [--no-provisional-status]` | Metadata schema, DDL, seeds, default settings rows | Deploy |
| `show-config` | Every setting with its value and source (`env` / `file` / `metadata` / `default`); metadata connection (no password) | Ops |
| `test-connections` | Connect to the metadata database and every active / referenced data connection; exit 1 if any fails | Deploy / ops |
| `validate-config` | Config checks; exit 1 on errors, logs `CONFIG_VALIDATION_FAILED` | CI / before activating config |
| `create-batches [--as-of]` | Batches for cron fires in the scheduler window | Cron |
| `catchup [--as-of]` | Batches for missed fires in the lookback | Hourly |
| `process-intake` | CYCLE_INIT / ADHOC_REQUEST / CORRECTION_REQUEST | Poll |
| `ingest-file --bucket --key [--version-id]` | One inbound object end to end | S3 event |
| `process-decisions` | Act on SQL approvals, rejections and waivers; promote approved reopens; auto re-trigger (D-41) | Poll |
| `evaluate-extracts [--as-of]` | Reconcile triggers; refresh extracts past the SLA hold; auto-trigger AUTO-eligible ones | Every 15 min |
| `refresh-extract --extract-id` | Recount / combine / evaluate one extract | Manual |
| `trigger-extract --extract-id --requested-by [--ack-warnings]` | Manual trigger (exit 2 if blocked) | Human |
| `resolve-trigger --trigger-id --outcome accepted/failed --actor [--job-run-ref]` | Resolve a trigger call whose outcome is unknown | Human |
| `notify` | Send pending notifications | Poll |
| `health` | Operational report (§15.3) | Ops |

**Exit codes:** 0 = ok, 1 = completed with problems, 2 = blocked or framework error.

## Approvals (manual SQL, D-12)

Use the templates in `src/framework/sql/templates/approvals.sql`.
- Every statement must report **1 row**.
- Approving a reopen requires naming the reviewed `Load_ID` (§12.4).
- `process-decisions` picks decisions up and writes the audit trail.

## Configuration

### Where values come from

| What | Precedence (highest first) |
|---|---|
| Runtime settings | `FRAMEWORK_<NAME>` env var > `[settings]` in the config file > `ComplianceFrameworkSetting` row > built-in default |
| Bootstrap settings (`CONFIG_FILE`, `METADATA_SCHEMA`, `AWS_REGION`) | env var > config file (never metadata) |
| Metadata database (config, control, audit tables) | `FRAMEWORK_DB_*` env vars > `[metadata_db]` > Secrets Manager secret |
| Data database `<name>` (staging, core) | `FRAMEWORK_CONN_<NAME>_*` env vars > `[connection:<name>]` > `ComplianceDbConnection` row > Secrets Manager secret |

- **Config file:** `FRAMEWORK_CONFIG_FILE`, otherwise `./framework.ini` if it exists. See `framework.ini.example`.
- **Connection keys** (env suffix / file key): `DSN`/`dsn`, `HOST`/`host`, `PORT`/`port`, `NAME`/`dbname` (or `database`), `USER`/`user`, `PASSWORD`/`password`, `PASSWORD_ENV`/`password_env` (name of a variable that holds the password), `SSLMODE`/`sslmode`, `CONNECT_TIMEOUT`/`connect_timeout`, `SECRET_NAME`/`secret_name`. Explicit keys override values in the DSN. The secret is JSON with `host`, `port`, `dbname`, `username`, `password` and optionally `sslmode`; it fills any field no higher layer set.
- **`<NAME>` in env vars** is the connection name upper-cased with non-alphanumerics replaced by `_` (e.g. `PRJA_DW` → `FRAMEWORK_CONN_PRJA_DW_HOST`).
- **Passwords** are never stored in `ComplianceDbConnection`: use `Secret_Nm` or `Password_Env_Var`.
- **`Target_Connection_Nm` NULL** means the tables are in the metadata database.
- **Change a setting in metadata:** `UPDATE ComplianceFrameworkSetting SET Setting_Val = '60', Updated_Dtts = now() WHERE Setting_Nm = 'LOCK_TIMEOUT_SECONDS';` (NULL = default). It applies at the next command start. `validate-config` reports values that do not convert.
- Direct connections only (D-55): no RDS Proxy / PgBouncer transaction pooling.

### Settings

Names below are the setting names; the env var is `FRAMEWORK_<NAME>`, the file key is the lower-case name, the metadata row is `Setting_Nm = <NAME>`.

| Setting | Default | Notes |
|---|---|---|
| `CONFIG_FILE` | `./framework.ini` if present | Bootstrap (env only). |
| `METADATA_SCHEMA` | `cms_compliance` | Bootstrap. Schema of all framework tables, including audit. |
| `AWS_REGION` | `us-east-1` | Bootstrap. |
| `OBJECT_STORE`, `LOCAL_STORE_ROOT` | `s3`, `./.local_store` | |
| `DEFAULT_QUARANTINE_URI` | placeholder | Destination for files that match no config. |
| `FILENAME_CASE_SENSITIVE` | `true` | **Q-03** |
| `FILE_EFFECTIVE_DATE_BASIS` | `RPT_START` | **Q-04** (`RPT_START` / `RPT_END`) |
| `SUPPORTED_FILE_TYPES` | `.txt,.csv` | **Q-02**. `.xlsx` / `.parquet` readers exist but are disabled by default. |
| `FILE_ENCODING`, `QUOTE_CHAR`, `EMPTY_AS_NULL` | `utf-8`, `"`, `true` | **Q-02** |
| `TRAILER_COUNT_CHECK`, `TRAILER_COUNT_REGEX` | `false`, `(\d+)` | **Q-02** (trailer layout) |
| `XLSX_SHEET`, `XLSX_HEADER_ROW` | `0`, `0` | **Q-02** |
| `SCHEDULER_WINDOW_HOURS`, `CATCHUP_LOOKBACK_DAYS`, `GO_LIVE_DATE` | `2`, `35`, — | **Q-14** |
| `LOCK_TIMEOUT_SECONDS`, `HEARTBEAT_STALE_MINUTES`, `TRIGGER_RECONCILE_MINUTES` | `300`, `30`, `15` | |
| `STRICT_WAIVER_AUTO_TRIGGER` | `false` | **Q-06** |
| `AUTO_RETRIGGER_AFTER_REOPEN` | `true` | D-41 / **Q-16** |
| `RETRY_FAILED_TRIGGERS_ON_SWEEP`, `CALL_RETRY_BACKOFF_SECONDS` | `false`, `5` | **Q-07**. Per-policy retry count: `Max_Call_Retry_Cnt`. |
| `HTTP_ACCEPTED_STATUS` | `200-299` | **Q-08** |
| `PARAM_DATE_FORMAT` | `%Y-%m-%d` | Date format for extract job parameters. |
| `ADHOC_ALLOW_ADD_SOURCE_BEFORE_TRIGGER` | `true` | **Q-05** |
| `CYCLE_INIT_EXISTING_BATCH` | `SKIP` | **Q-18** (`SKIP` / `FAIL`) |
| `RULE_ENGINE`, `GRE_ENTRYPOINT` | `gre`, — | **Q-12**. `none` disables rules; `module:Class` plugs in another engine. |
| `NOTIFY_BACKEND`, `NOTIFY_FROM_EMAIL`, `DEFAULT_NOTIFY_EMAILS` | `log`, —, — | D-54 (`aws` = SES/SNS) |
| `SPARK_JDBC_URL`, `SPARK_JDBC_PROPERTIES`, `SPARK_WRITE_PARTITIONS`, `SPARK_BATCH_SIZE` | —, `{}`, `4`, `10000` | For `Engine_Cd = SPARK`. The JDBC URL, user and password default to the file config's target connection. |

## GRE integration contract (Q-12)

Set `FRAMEWORK_GRE_ENTRYPOINT=package.module:function` to a function with this signature:

```python
def run_rules(data_conn, metadata_conn, rule_group: str, rule_variant: str, run_params: dict) -> list[dict]:
    # one dict per executed rule: {"rule_ref": "R1", "passed": True, "detail": None}
    # raise on technical failure (treated as ERROR -> retry, never as a data failure)
```

`data_conn` is the database with the staging/core tables (`run_params["target_connection_nm"]`, NULL = metadata database); `metadata_conn` is the metadata database. They are the same connection when the tables are in the metadata database (**Q-19**).

`run_params` contents by scope:
- **FILE_LEVEL:** `btch_id`, `load_id`, the staging table, project/table/source/run type and report dates.
- **PERIOD_LEVEL:** `btch_id_list`, `load_id_list`, the core table and the extract grain.

The framework applies GATE/ANNOTATE itself: the mode comes from the file config for FILE_LEVEL (D-44) and from the extract policy for PERIOD_LEVEL (D-63).

## Implementation status and open items

**Implemented and tested:** every flow in design §7 (P1–P13), the §8 decision tables, the §9 templates, §10 promotion and combine, §11 eligibility, trigger, close, re-trigger and reconciliation, §12 locks and idempotency, the validator, notifications and health.

**Not yet verified:**
- **Spark engine** (`load/engine/spark_engine.py`). It is written against PySpark 3.x but was not run: PySpark and the PostgreSQL JDBC driver were not installable in the build environment. Test it on Glue/Spark before enabling `Engine_Cd = SPARK`.
- **AWS adapters** (S3 store, Glue connector, SES/SNS, Secrets Manager connection secrets). They are written against boto3; unit tests cover the Glue connector with a fake client only.

**Waiting on decisions** (defaults are configurable; see the design doc §16):

| Question | What it affects |
|---|---|
| Q-01 | The final `Req_Stat` list. Replace `sql/seed/030_request_status_PROVISIONAL.sql`; no code change is needed. |
| Q-02 | File types, encoding and trailer layout. |
| Q-03 | Case sensitivity and `{RUNTY}` characters. |
| Q-04 | Effective-date basis. |
| Q-05, Q-06, Q-07, Q-08 | Ad-hoc additions, waiver auto-trigger, call retries/idempotency, HTTP auth. |
| Q-11 | PostgreSQL version and `btree_gist`. |
| Q-12 | GRE entry point. |
| Q-13, Q-14, Q-15 | Orchestration, rollout and security. |
| Q-16, Q-17, Q-18 | Auto re-trigger, alerting, cycle-init duplicates. |
| Q-19 | Where GRE reads/writes when the data database differs from the metadata database. |

**Not built** (design Phase 2): Glue / Step Functions wrappers, Terraform for the new jobs, CI pipeline.
