"""
glue_job_metadata_load.py
=============================================================================
CMS Compliance Framework — single Glue job to load / update all "supporting
metadata" tables in PostgreSQL:

    compliance_source_system         (reference, one-time seed)
    compliance_run_type              (reference, one-time seed)
    compliance_dataset_source_xwalk  (reference, one-time seed + manual edits)
    compliance_request_intake        (human-entered, updated as processed)
    cms_compliance_exceptions_audit  (append-only narrative log)

These are the tables docs/design/schema-design.md calls out as "one-time seed
or ad-hoc updates" — as opposed to ComplianceRequestControl / BatchOverride /
FileDetail / ExtractControl, which the orchestration pipeline itself writes.

DESIGN
------
One job, driven entirely by TABLE_CONFIG below. Each run:
  1. (optional) applies ddl/create_metadata_tables.sql so the schema exists.
  2. For every table in scope, reads one input file (CSV) from S3 named
     "<table_key>.csv" under --S3_INPUT_PATH.
  3. Loads it into Postgres using the mode configured for that table:
       - "upsert"      INSERT ... ON CONFLICT (pk) DO UPDATE  (reference /
                       intake tables — first run inserts, later runs with an
                       edited CSV update the same rows in place)
       - "insert_only" INSERT ... ON CONFLICT (pk) DO NOTHING (append-only
                       audit log — rows are never modified once written)

This lets ONE job handle the initial one-time load and every later manual /
ad-hoc update: re-running it with a refreshed CSV (add a row, correct a row,
flip Active_Ind to N, mark an intake row Processed_Ind='Y', append new
exception events) is the update mechanism — no separate "load" vs "update"
jobs to maintain.

This is a Glue **Python Shell** job (not Spark) — these are small
metadata/reference tables, so a lightweight pandas + psycopg2 job is the
right tool: faster startup, cheaper, and simpler upsert semantics than
going through a Spark DataFrame write.

JOB PARAMETERS (set as Glue job arguments, all as --KEY VALUE)
  --S3_INPUT_PATH   s3://<bucket>/<prefix>/            (folder holding the CSVs)
  --SECRET_NAME     <Secrets Manager secret id>         (Postgres credentials)
  --TABLES          (optional) comma-separated subset of table keys to run;
                     default = all five tables
  --RUN_DDL         (optional) "true"/"false", default "true" — applies
                     ddl/create_metadata_tables.sql (also uploaded to S3
                     alongside the CSVs, see --DDL_S3_PATH)
  --DDL_S3_PATH     (optional) s3://<bucket>/<prefix>/create_metadata_tables.sql
                     required only when --RUN_DDL is true

Glue job setup notes:
  - Job type: Python Shell, Python 3.9+
  - Additional python modules (--additional-python-modules):
        psycopg2-binary,pandas
  - IAM role needs: s3:GetObject on the input path, and
        secretsmanager:GetSecretValue on SECRET_NAME
  - The Secrets Manager secret should be a JSON object:
        {"host": "...", "port": 5432, "dbname": "...", "user": "...", "password": "..."}
  - Schedule via a Glue trigger (on-demand for one-time loads, or a schedule
    for recurring ad-hoc-update runs) as needed.
=============================================================================
"""

import io
import sys
import logging

import boto3
import pandas as pd
import psycopg2
import psycopg2.extras
from awsglue.utils import getResolvedOptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cms_metadata_load")

# -----------------------------------------------------------------------------
# Table registry — the single source of truth for what this job loads and how.
# -----------------------------------------------------------------------------
TABLE_CONFIG = {
    "compliance_source_system": {
        "primary_key": ["src_cd"],
        "columns": ["src_cd", "src_nm", "src_ty", "active_ind"],
        "mode": "upsert",
    },
    "compliance_run_type": {
        "primary_key": ["run_ty"],
        "columns": ["run_ty", "run_ty_desc", "sla_days"],
        "mode": "upsert",
    },
    "compliance_dataset_source_xwalk": {
        "primary_key": ["project_cd", "table_nm", "src_cd", "run_ty", "cmplnc_vrsn"],
        "columns": [
            "project_cd", "table_nm", "src_cd", "run_ty", "cmplnc_vrsn",
            "carry_fwd_elig_ind",
        ],
        "mode": "upsert",
    },
    "compliance_request_intake": {
        "primary_key": ["intake_id"],
        "columns": [
            "intake_id", "project_cd", "table_nm", "src_cd", "req_dt_key",
            "req_ty", "requested_by", "requested_dtts", "rsn",
            "processed_ind", "processed_dtts",
        ],
        "mode": "upsert",
    },
    "cms_compliance_exceptions_audit": {
        "primary_key": ["event_id"],
        "columns": [
            "event_id", "btch_id", "event_ctgy", "event_ty", "sevrty",
            "actor", "event_dtts", "description",
        ],
        "mode": "insert_only",
    },
}

DB_SCHEMA = "cms_compliance"


def get_db_connection(secret_name: str):
    """Fetch Postgres credentials from Secrets Manager and open a connection."""
    import json

    sm = boto3.client("secretsmanager")
    secret = json.loads(sm.get_secret_value(SecretId=secret_name)["SecretString"])
    conn = psycopg2.connect(
        host=secret["host"],
        port=secret.get("port", 5432),
        dbname=secret["dbname"],
        user=secret["user"],
        password=secret["password"],
        connect_timeout=10,
    )
    conn.autocommit = False
    return conn


def read_csv_from_s3(s3_input_path: str, table_key: str) -> pd.DataFrame:
    """Read s3://.../<table_key>.csv into a DataFrame. Empty file -> empty frame."""
    s3 = boto3.client("s3")
    bucket, _, prefix = s3_input_path.replace("s3://", "").partition("/")
    key = f"{prefix.rstrip('/')}/{table_key}.csv"
    logger.info("Reading s3://%s/%s", bucket, key)
    obj = s3.get_object(Bucket=bucket, Key=key)
    df = pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=str, keep_default_na=False)
    df = df.replace({"": None})
    return df


def apply_ddl(conn, s3_ddl_path: str):
    """Run the CREATE TABLE IF NOT EXISTS script so the schema is guaranteed present."""
    s3 = boto3.client("s3")
    bucket, _, key = s3_ddl_path.replace("s3://", "").partition("/")
    logger.info("Applying DDL from s3://%s/%s", bucket, key)
    ddl_sql = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    with conn.cursor() as cur:
        cur.execute(ddl_sql)
    conn.commit()
    logger.info("DDL applied.")


def upsert_table(conn, table_key: str, df: pd.DataFrame, config: dict) -> int:
    """
    Generic upsert (or insert-only append) for one metadata table, driven by
    TABLE_CONFIG. Returns number of rows sent to Postgres.
    """
    if df.empty:
        logger.info("[%s] no rows in input file, skipping.", table_key)
        return 0

    columns = config["columns"]
    pk_cols = config["primary_key"]
    mode = config["mode"]
    target_table = f"{DB_SCHEMA}.{table_key}"

    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"[{table_key}] input CSV is missing columns: {missing}")

    records = list(df[columns].itertuples(index=False, name=None))

    col_list = ", ".join(columns)
    placeholder_row = "(" + ", ".join(["%s"] * len(columns)) + ")"

    if mode == "insert_only":
        # Append-only audit log: never touch a row once it exists.
        sql = (
            f"INSERT INTO {target_table} ({col_list}) VALUES %s "
            f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
        )
    elif mode == "upsert":
        update_cols = [c for c in columns if c not in pk_cols]
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        set_clause += ", updated_dtts = now()" if update_cols else "updated_dtts = now()"
        sql = (
            f"INSERT INTO {target_table} ({col_list}) VALUES %s "
            f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
        )
    else:
        raise ValueError(f"[{table_key}] unknown load mode: {mode}")

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, records, template=placeholder_row, page_size=500)
        row_count = cur.rowcount

    conn.commit()
    logger.info("[%s] mode=%s rows_in_file=%d rows_affected=%d", table_key, mode, len(records), row_count)
    return len(records)


def main():
    args = getResolvedOptions(
        sys.argv,
        ["S3_INPUT_PATH", "SECRET_NAME"],
    )
    optional = getResolvedOptions(
        sys.argv,
        [],
    )

    # Manually pull optional args so the job doesn't fail when they're absent.
    def opt(name, default=None):
        flag = f"--{name}"
        if flag in sys.argv:
            return sys.argv[sys.argv.index(flag) + 1]
        return default

    s3_input_path = args["S3_INPUT_PATH"]
    secret_name = args["SECRET_NAME"]
    tables_arg = opt("TABLES")
    run_ddl = opt("RUN_DDL", "true").lower() == "true"
    ddl_s3_path = opt("DDL_S3_PATH")

    table_keys = [t.strip() for t in tables_arg.split(",")] if tables_arg else list(TABLE_CONFIG.keys())
    unknown = [t for t in table_keys if t not in TABLE_CONFIG]
    if unknown:
        raise ValueError(f"--TABLES contains unknown table key(s): {unknown}. Known: {list(TABLE_CONFIG)}")

    logger.info("Starting CMS metadata load. Tables in scope: %s", table_keys)

    conn = get_db_connection(secret_name)
    try:
        if run_ddl:
            if not ddl_s3_path:
                raise ValueError("RUN_DDL is true but --DDL_S3_PATH was not provided.")
            apply_ddl(conn, ddl_s3_path)

        summary = {}
        for table_key in table_keys:
            config = TABLE_CONFIG[table_key]
            try:
                df = read_csv_from_s3(s3_input_path, table_key)
                n = upsert_table(conn, table_key, df, config)
                summary[table_key] = {"status": "OK", "rows": n}
            except Exception as exc:  # noqa: BLE001 - log and continue with other tables
                conn.rollback()
                logger.exception("[%s] failed: %s", table_key, exc)
                summary[table_key] = {"status": "FAILED", "error": str(exc)}

        logger.info("Load summary: %s", summary)
        if any(v["status"] == "FAILED" for v in summary.values()):
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
