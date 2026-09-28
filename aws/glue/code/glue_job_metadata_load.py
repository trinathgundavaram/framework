"""
glue_job_metadata_load.py
=============================================================================
CMS Compliance Framework - generic Glue job that upserts ONE CSV file into
ONE Postgres table per run. No config file involved (no table_config.json) -
which table, which file, and the primary key are passed directly as job
parameters at run time: AWS console "Run job" -> Job parameters, or
`aws glue start-job-run --arguments`. The same job/script handles any table
- nothing here is hardcoded to a specific table.

Assumes the DDL/table already exists (this job never creates schema).

Connection pattern
-------------------
Uses the RdsClient (Secrets Manager -> pg8000) helper from rds_conn.py,
uploaded to S3 alongside this script (see aws/glue/glue-job.tf).

DESIGN
------
- The input CSV's header IS the column list to load - by convention (see
  src/framework/sql/schema.sql) every table's file layout matches the
  table layout exactly except for the audit columns the DB itself owns
  (created_dtts / updated_dtts / loaded_dtts / created_by / updated_by).
  Those never appear in the file, so there's nothing to strip - the job
  still guards against one showing up in a file by dropping any column
  whose name is in AUDIT_COLUMNS before building SQL.

- One run = one table = one file:
    - "upsert"      INSERT ... ON CONFLICT (primary_key) DO UPDATE  - every
                    non-PK column in the file is set from EXCLUDED, and
                    updated_dtts (if it's one of the audit columns in scope)
                    is bumped to now().
    - "insert_only" INSERT ... ON CONFLICT (primary_key) DO NOTHING - for
                    append-only logs (e.g. an audit/exception table).

  Re-running the job against the same table with a refreshed CSV (add a
  row, correct a row, flip a flag) is the update mechanism - there's no
  separate "load" vs "update" job. Loading the framework's configuration
  tables (or any other table) is just one separate manual run per table
  of this same job with different --TABLE_NAME / --S3_FILE_NAME /
  --PRIMARY_KEY values - see the README for the exact commands.

This is a Glue **Python Shell** job (not Spark) - these are small
metadata/reference tables, so a lightweight pandas + pg8000 job is the
right tool.

JOB PARAMETERS (set as Glue job arguments, all as --KEY VALUE)
  --TABLE_NAME      Schema-qualified Postgres table to load, e.g.
                     cms_compliance.compliancesourcesystem. Must already exist
                     (framework table names are unquoted, so Postgres stores them lower-case).
  --S3_INPUT_PATH   s3://<bucket>/<prefix>/    Folder the input file lives in.
  --S3_FILE_NAME    <file_name>.csv            File inside that folder to load
                     (the job reads s3://<bucket>/<prefix>/<file_name>).
  --PRIMARY_KEY     Comma-separated primary key column(s) for the ON CONFLICT
                     target, e.g. src_id
                     or project_cd,table_nm,src_id,run_ty,effective_start_dt_key
  --MODE            Optional: "upsert" (default) or "insert_only" (append-only
                     table - audit/exception logs).
  --AUDIT_COLUMNS   Optional comma-separated list of audit columns the table
                     owns and this job must never load/overwrite, even if one
                     shows up in the file. REPLACES the default list (doesn't
                     add to it). Default: created_dtts,updated_dtts,loaded_dtts,
                     created_by,updated_by
  --RDS_SECRET_NM   Secrets Manager secret id holding the Postgres credentials.
  --RDS_DATABASE_NM Postgres database name RdsClient connects to.
  --REGION          Optional AWS region for the Secrets Manager lookup, default us-east-1.

Glue job setup notes (see aws/glue/glue-job.tf):
  - Job type: Python Shell, Python 3.9
  - --additional-python-modules: pg8000,pandas
  - IAM role needs: s3:GetObject on the input path, and
        secretsmanager:GetSecretValue on RDS_SECRET_NM
  - The Secrets Manager secret is a JSON object:
        {"host": "...", "port": 5432, "dbname": "...", "username": "...", "password": "..."}
=============================================================================
"""

import io
import re
import sys
import logging

import boto3
import pandas as pd
from awsglue.utils import getResolvedOptions

from rds_conn import RdsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cms_metadata_load")

# Columns the database itself owns - never loaded from the file even if
# present. Override with --AUDIT_COLUMNS to replace this list for a run
# whose table uses different audit column names.
DEFAULT_AUDIT_COLUMNS = {
    "created_dtts", "updated_dtts", "loaded_dtts", "created_by", "updated_by",
}

# PostgreSQL accepts at most 65535 bind parameters per statement; a larger file is sent in chunks.
MAX_PARAMS = 65535

# Table and column names come from job arguments and the CSV header and are placed in the SQL
# unquoted (PostgreSQL folds them to lower case), so each must be a plain identifier.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def check_identifiers(what: str, names) -> None:
    bad = [n for n in names if not _IDENT.match(n)]
    if bad:
        raise ValueError(f"{what}: not a plain SQL identifier: {bad}")


def read_csv_from_s3(s3_input_path: str, file_name: str) -> pd.DataFrame:
    """Read s3://.../<file_name> into a DataFrame. Empty file -> empty frame."""
    s3 = boto3.client("s3")
    bucket, _, prefix = s3_input_path.replace("s3://", "").partition("/")
    key = f"{prefix.rstrip('/')}/{file_name}"
    logger.info("Reading s3://%s/%s", bucket, key)
    obj = s3.get_object(Bucket=bucket, Key=key)
    df = pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=str, keep_default_na=False)
    return df.replace({"": None})


def upsert_file(conn, table: str, s3_input_path: str, file_name: str,
                 pk_cols: list, mode: str, audit_columns: set) -> int:
    """
    Generic upsert (or insert-only append) of one CSV file into one Postgres
    table. Has no per-table knowledge - the file's own header defines which
    columns get loaded. Returns number of rows sent to Postgres.
    """
    df = read_csv_from_s3(s3_input_path, file_name)
    if df.empty:
        logger.info("[%s] no rows in %s, skipping.", table, file_name)
        return 0

    # File layout = table layout minus audit columns -> the header itself
    # is the column list, guarded against an audit column sneaking in.
    columns = [c for c in df.columns if c not in audit_columns]

    missing_pk = [c for c in pk_cols if c not in columns]
    if missing_pk:
        raise ValueError(f"[{table}] file {file_name} is missing primary key column(s): {missing_pk}")

    check_identifiers(f"[{table}] --TABLE_NAME", table.split("."))
    check_identifiers(f"[{table}] columns of {file_name}", columns)
    check_identifiers(f"[{table}] --PRIMARY_KEY", pk_cols)
    check_identifiers(f"[{table}] --AUDIT_COLUMNS", audit_columns)

    records = list(df[columns].itertuples(index=False, name=None))
    col_list = ", ".join(columns)
    row_placeholder = "(" + ", ".join(["%s"] * len(columns)) + ")"

    if mode not in ("upsert", "insert_only"):
        raise ValueError(f"[{table}] unknown --MODE: {mode} (expected upsert or insert_only)")

    # insert_only (append-only log) never touches a row once it exists. upsert sets every non-PK
    # column from EXCLUDED and bumps updated_dtts; a pure-PK table has nothing to update -> DO NOTHING.
    set_parts = []
    if mode == "upsert":
        set_parts = [f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_cols]
        if "updated_dtts" in audit_columns:
            set_parts.append("updated_dtts = now()")
    action = f"DO UPDATE SET {', '.join(set_parts)}" if set_parts else "DO NOTHING"

    # One statement when the file fits in the parameter limit (every file that loaded before still
    # loads exactly as before); otherwise chunks, all committed together below.
    chunk = max(1, MAX_PARAMS // len(columns))
    row_count = 0
    cur = conn.cursor()
    try:
        for start in range(0, len(records), chunk):
            part = records[start:start + chunk]
            values_sql = ", ".join([row_placeholder] * len(part))
            sql = f"INSERT INTO {table} ({col_list}) VALUES {values_sql} ON CONFLICT ({', '.join(pk_cols)}) {action}"
            cur.execute(sql, [value for row in part for value in row])
            row_count += cur.rowcount
    finally:
        cur.close()

    conn.commit()
    logger.info("[%s] mode=%s rows_in_file=%d rows_affected=%d", table, mode, len(records), row_count)
    return len(records)


def main():
    args = getResolvedOptions(
        sys.argv,
        ["TABLE_NAME", "S3_INPUT_PATH", "S3_FILE_NAME", "PRIMARY_KEY",
         "RDS_SECRET_NM", "RDS_DATABASE_NM"],
    )

    # Manually pull optional args so the job doesn't fail when they're absent.
    def opt(name, default=None):
        flag = f"--{name}"
        if flag in sys.argv:
            return sys.argv[sys.argv.index(flag) + 1]
        return default

    table = args["TABLE_NAME"]
    s3_input_path = args["S3_INPUT_PATH"]
    file_name = args["S3_FILE_NAME"]
    pk_cols = [c.strip() for c in args["PRIMARY_KEY"].split(",") if c.strip()]
    secret_name = args["RDS_SECRET_NM"]
    db_name = args["RDS_DATABASE_NM"]

    region = opt("REGION", "us-east-1")
    mode = opt("MODE", "upsert")
    audit_columns_arg = opt("AUDIT_COLUMNS")
    audit_columns = (
        {c.strip() for c in audit_columns_arg.split(",") if c.strip()}
        if audit_columns_arg else set(DEFAULT_AUDIT_COLUMNS)
    )

    if not pk_cols:
        raise ValueError("--PRIMARY_KEY must list at least one column.")

    logger.info(
        "Starting load: table=%s file=s3://.../%s pk=%s mode=%s",
        table, file_name, pk_cols, mode,
    )

    rds_client = RdsClient(secret_name=secret_name, rds_database_name=db_name, region=region)
    conn = rds_client.connect()
    try:
        n = upsert_file(conn, table, s3_input_path, file_name, pk_cols, mode, audit_columns)
        logger.info("Done. table=%s rows_in_file=%d", table, n)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
