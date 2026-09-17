# CMS Compliance Framework: Python Package

This is the implementation of [`docs/design/cms-compliance-framework-design.md`](design/cms-compliance-framework-design.md) (v3.1).
- **Project-agnostic:** every project, table, source and run type is configuration. The code has no project-specific branches.
- **Filename-driven:** incoming files are recognised by the templates stored in `ComplianceSourceFileConfig`.
- **Phase 1 scope (design §3):** a local, orchestration-agnostic package with a CLI. Glue or Step Functions wrappers come later (D-13).

## Layout

```
pyproject.toml                  package metadata; `framework` console script
docker-compose.yml              local PostgreSQL 16 for development/tests
src/framework/
  cli.py                        entry points (one command = one service)
  app.py                        service wiring
  settings.py                   FRAMEWORK_* environment settings
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
tests/                          109 tests (unit + PostgreSQL integration)
```

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d
export FRAMEWORK_DB_DSN=postgresql://framework:framework@localhost:5432/framework
export TEST_DATABASE_URL=$FRAMEWORK_DB_DSN
pytest                                 # tests recreate the cms_compliance schema - use a scratch database

framework init-db                      # schema + seeds (DDL is skipped if the schema already exists)
export FRAMEWORK_OBJECT_STORE=local FRAMEWORK_LOCAL_STORE_ROOT=./.local_store FRAMEWORK_RULE_ENGINE=none
framework validate-config
```

Python 3.10+ (tested on 3.10 and 3.11) and PostgreSQL 14+ with `btree_gist` (tested on 16).

## Onboarding a project (config only)

1. **Create the target tables.**
   - Staging: business columns in file order, plus `btch_id`, `load_id`, `src_file_nm`, `stg_load_dtts`.
   - Core: business columns plus `btch_id`, `load_id`, `current_ind`, `load_dtts`, `end_dtts`.
   - See the DDL comment at the end of `001_schema.sql`.
2. **Insert config rows**, in this order:
   1. `ComplianceSourceSystem`
   2. `ComplianceRunType` (`SLA_Days` ≥ 1)
   3. `ComplianceDataSetSourceXwalk` (one row per source × run type, effective-dated; ROUTINE rows need a cron and a period strategy)
   4. `ComplianceSourceFileConfig` (one active row per project/table/source: template, aliases, file format, engine, GATE/ANNOTATE, S3 paths, targets)
   5. `ComplianceExtractPolicy` + `ComplianceExtractJobParam` (one per project/table/run type)
   6. `ComplianceRuleBinding`
3. **Run `framework validate-config`.** It must report no `ERROR` issues.

Filename template example (literal text plus exactly one of each placeholder, separated by literals):

```
{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt
```

## Commands

| Command | Purpose | Typical trigger |
|---|---|---|
| `init-db [--no-provisional-status]` | Schema and seeds | Deploy |
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

## Settings (`FRAMEWORK_*` environment variables)

| Variable | Default | Notes |
|---|---|---|
| `DB_DSN` / `DB_SECRET_NAME` | — | Direct connection only (D-55). The secret JSON holds host, port, dbname, username, password. |
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
| `SPARK_JDBC_URL`, `SPARK_JDBC_PROPERTIES`, `SPARK_WRITE_PARTITIONS`, `SPARK_BATCH_SIZE` | —, `{}`, `4`, `10000` | For `Engine_Cd = SPARK`. |

## GRE integration contract (Q-12)

Set `FRAMEWORK_GRE_ENTRYPOINT=package.module:function` to a function with this signature:

```python
def run_rules(conn, rule_group: str, rule_variant: str, run_params: dict) -> list[dict]:
    # one dict per executed rule: {"rule_ref": "R1", "passed": True, "detail": None}
    # raise on technical failure (treated as ERROR -> retry, never as a data failure)
```

`run_params` contents by scope:
- **FILE_LEVEL:** `btch_id`, `load_id`, the staging table, project/table/source/run type and report dates.
- **PERIOD_LEVEL:** `btch_id_list`, `load_id_list`, the core table and the extract grain.

The framework applies GATE/ANNOTATE itself: the mode comes from the file config for FILE_LEVEL (D-44) and from the extract policy for PERIOD_LEVEL (D-63).

## Implementation status and open items

**Implemented and tested:** every flow in design §7 (P1–P13), the §8 decision tables, the §9 templates, §10 promotion and combine, §11 eligibility, trigger, close, re-trigger and reconciliation, §12 locks and idempotency, the validator, notifications and health.

**Not yet verified:**
- **Spark engine** (`load/engine/spark_engine.py`). It is written against PySpark 3.x but was not run: PySpark and the PostgreSQL JDBC driver were not installable in the build environment. Test it on Glue/Spark before enabling `Engine_Cd = SPARK`.
- **AWS adapters** (S3 store, Glue connector, SES/SNS, Secrets Manager DSN). They are written against boto3; unit tests cover the Glue connector with a fake client only.

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

**Not built** (design Phase 2): Glue / Step Functions wrappers, Terraform for the new jobs, CI pipeline.
