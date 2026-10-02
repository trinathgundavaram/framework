"""Glue job: upsert one CSV from S3 into one Postgres table."""

import csv
import io
import logging
import re
import sys

import boto3
from awsglue.utils import getResolvedOptions

from rds_conn import RdsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cms_metadata_load")

DEFAULT_AUDIT_COLUMNS = {"created_dtts", "updated_dtts", "loaded_dtts", "created_by", "updated_by"}
MAX_PARAMS = 65535
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def check_identifiers(what: str, names) -> None:
    bad = [n for n in names if not _IDENT.match(n)]
    if bad:
        raise ValueError(f"{what}: not a plain SQL identifier: {bad}")


def read_csv_from_s3(s3_input_path: str, file_name: str) -> tuple:
    """(header, rows) of s3://.../<file_name>; empty values become NULL."""
    bucket, _, prefix = s3_input_path.replace("s3://", "").partition("/")
    key = f"{prefix.rstrip('/')}/{file_name}"
    logger.info("Reading s3://%s/%s", bucket, key)
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(body))
    header = [c.strip() for c in next(reader, [])]
    rows = [[v if v != "" else None for v in r] for r in reader if any(r)]
    bad = [i for i, r in enumerate(rows, 2) if len(r) != len(header)]
    if bad:
        raise ValueError(f"{file_name}: lines {bad[:10]} do not have {len(header)} values")
    return header, rows


def upsert_file(conn, table: str, s3_input_path: str, file_name: str,
                pk_cols: list, mode: str, audit_columns: set) -> int:
    """Upsert (or insert-only append) one CSV file into one Postgres table."""
    if mode not in ("upsert", "insert_only"):
        raise ValueError(f"[{table}] unknown --MODE: {mode} (expected upsert or insert_only)")
    header, rows = read_csv_from_s3(s3_input_path, file_name)
    if not rows:
        logger.info("[%s] no rows in %s, skipping.", table, file_name)
        return 0

    keep = [i for i, c in enumerate(header) if c not in audit_columns]
    columns = [header[i] for i in keep]
    missing_pk = [c for c in pk_cols if c not in columns]
    if missing_pk:
        raise ValueError(f"[{table}] file {file_name} is missing primary key column(s): {missing_pk}")

    check_identifiers(f"[{table}] --TABLE_NAME", table.split("."))
    check_identifiers(f"[{table}] columns of {file_name}", columns)
    check_identifiers(f"[{table}] --PRIMARY_KEY", pk_cols)
    check_identifiers(f"[{table}] --AUDIT_COLUMNS", audit_columns)

    records = [[r[i] for i in keep] for r in rows]
    set_parts = []
    if mode == "upsert":
        set_parts = [f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_cols]
        if "updated_dtts" in audit_columns:
            set_parts.append("updated_dtts = now()")
    action = f"DO UPDATE SET {', '.join(set_parts)}" if set_parts else "DO NOTHING"
    row_placeholder = "(" + ", ".join(["%s"] * len(columns)) + ")"

    chunk = max(1, MAX_PARAMS // len(columns))
    row_count = 0
    cur = conn.cursor()
    try:
        for start in range(0, len(records), chunk):
            part = records[start:start + chunk]
            sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES {', '.join([row_placeholder] * len(part))} "
                   f"ON CONFLICT ({', '.join(pk_cols)}) {action}")
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
        ["TABLE_NAME", "S3_INPUT_PATH", "S3_FILE_NAME", "PRIMARY_KEY", "RDS_SECRET_NM"],
    )

    def opt(name, default=None):
        flag = f"--{name}"
        if flag in sys.argv[:-1]:
            return sys.argv[sys.argv.index(flag) + 1]
        return default

    table = args["TABLE_NAME"]
    schema = opt("METADATA_SCHEMA")
    if schema and "." not in table:
        table = f"{schema}.{table}"
    file_name = args["S3_FILE_NAME"]
    pk_cols = [c.strip() for c in args["PRIMARY_KEY"].split(",") if c.strip()]
    if not pk_cols:
        raise ValueError("--PRIMARY_KEY must list at least one column.")
    mode = opt("MODE", "upsert")
    audit_arg = opt("AUDIT_COLUMNS")
    audit_columns = {c.strip() for c in audit_arg.split(",") if c.strip()} if audit_arg else set(DEFAULT_AUDIT_COLUMNS)

    logger.info("Starting load: table=%s file=%s pk=%s mode=%s", table, file_name, pk_cols, mode)
    conn = RdsClient(args["RDS_SECRET_NM"], opt("RDS_DATABASE_NM"), opt("REGION", "us-east-1")).connect()
    try:
        n = upsert_file(conn, table, args["S3_INPUT_PATH"], file_name, pk_cols, mode, audit_columns)
        logger.info("Done. table=%s rows_in_file=%d", table, n)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
