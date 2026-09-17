"""Staging load and core promotion (design §9.4, §10).

* file reading    structural checks C12-C14 (D-58, D-59); values are text, Postgres casts on COPY
* stage()         delete the batch's staging rows (D-05) and load one file tagged with its Load_ID;
                  engine PANDAS (python reader + COPY) or SPARK (JDBC) - job setting LOAD_ENGINE (D-62)
* swap()          disable current core rows of the batch and append the load's staged rows (D-01)
* restage()       re-stage an approved reopen file from the S3 archive (D-53)

Staging and core tables live in the framework database (schema-qualified in the file config).
"""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import psycopg
from psycopg import sql

from .common import ConfigError, FileRejected, RowCountMismatch, TechnicalFailure

if TYPE_CHECKING:
    from .adapters import ObjectStore
    from .config import FileConfig
    from .settings import Settings

STAGING_FRAMEWORK_COLS = ("btch_id", "load_id", "src_file_nm", "stg_load_dtts")
CORE_FRAMEWORK_COLS = ("btch_id", "load_id", "current_ind", "load_dtts", "end_dtts")


# ============================================================================ table introspection
@dataclass(frozen=True)
class Column:
    name: str
    auto: bool          # identity / generated / serial - never inserted


def columns(conn: psycopg.Connection, schema: str, table: str) -> list[Column]:
    rows = conn.execute(
        """SELECT column_name, is_identity, is_generated, column_default FROM information_schema.columns
            WHERE table_schema = lower(%s) AND table_name = lower(%s) ORDER BY ordinal_position""",
        (schema, table)).fetchall()
    return [Column(r["column_name"], r["is_identity"] == "YES" or r["is_generated"] == "ALWAYS"
                   or "nextval(" in (r["column_default"] or "")) for r in rows]


def _table_columns(conn, schema: str, table: str, required: tuple[str, ...], kind: str) -> list[Column]:
    cols = columns(conn, schema, table)
    if not cols:
        raise LookupError(f"{kind} table {schema}.{table} not found")
    missing = [c for c in required if c not in {x.name for x in cols}]
    if missing:
        raise LookupError(f"{kind} table {schema}.{table} lacks framework columns {missing}")
    return cols


def staging_business_columns(conn, schema: str, table: str) -> list[str]:
    return [c.name for c in _table_columns(conn, schema, table, STAGING_FRAMEWORK_COLS, "staging")
            if c.name not in STAGING_FRAMEWORK_COLS]


def core_insert_columns(conn, cfg: "FileConfig") -> list[str]:
    """Staging business columns that exist in core, minus identity/generated/serial columns (D-61)."""
    core = _table_columns(conn, cfg.core_schema_nm, cfg.core_tblnm, CORE_FRAMEWORK_COLS, "core")
    usable = {c.name for c in core if not c.auto and c.name not in CORE_FRAMEWORK_COLS}
    return [c for c in staging_business_columns(conn, cfg.stg_schema_nm, cfg.stg_tblnm) if c in usable]


def _ident(schema: str, table: str) -> sql.Identifier:
    return sql.Identifier(schema.lower(), table.lower())


# ============================================================================ file reading
_DELIMS = {"TAB": "\t", "\\T": "\t", "PIPE": "|", "COMMA": ",", "SEMICOLON": ";"}
_STD_TERMINATORS = {None, "", "\\N", "\\R\\N", "LF", "CRLF", "\n", "\r\n"}


@dataclass
class ReadResult:
    rows: list[list[Optional[str]]]
    header_col_count: Optional[int]
    trailer_count: Optional[int]

    @property
    def data_row_count(self) -> int:
        return len(self.rows)


def delimiter_for(cfg: "FileConfig") -> str:
    d = cfg.delmtr_cd
    if d is None or d == "":
        return ","
    return _DELIMS.get(d.upper(), d)


def _check_terminator(cfg: "FileConfig") -> None:
    lt = cfg.line_term_cd
    if lt is not None and lt.upper() not in _STD_TERMINATORS and lt not in _STD_TERMINATORS:
        raise ConfigError(f"Line_Term_Cd {lt!r} is not supported (open question Q-02)")


def sanitize_db_error(msg: str) -> str:
    """Remove quoted values from database error text so no file content reaches audit logs."""
    return re.sub(r'"[^"]*"', '"***"', msg or "")[:500]


def _normalise(values: list[str], empty_as_null: bool) -> list[Optional[str]]:
    return [None if (empty_as_null and v == "") else v for v in values]


def read_delimited(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> ReadResult:
    _check_terminator(cfg)
    delim = delimiter_for(cfg)
    try:
        with open(path, "r", encoding=settings.file_encoding, newline="") as f:
            text = f.read()
    except UnicodeDecodeError as e:
        raise FileRejected("FILE_PARSE_ERROR", f"file is not valid {settings.file_encoding}: {e.reason}")
    lines = text.splitlines(keepends=True)
    while lines and lines[-1].strip() == "":
        lines.pop()
    header_cols = None
    trailer_count = None
    if cfg.has_trailer:
        if not lines:
            raise FileRejected("FILE_PARSE_ERROR", "trailer expected but file is empty")
        trailer = lines.pop()
        if settings.trailer_count_check:
            m = re.search(settings.trailer_count_regex, trailer)
            if not m:
                raise FileRejected("FILE_TRAILER_COUNT_MISMATCH", "trailer record count not found")
            trailer_count = int(m.group(1))
    reader = csv.reader(io.StringIO("".join(lines)), delimiter=delim, quotechar=settings.quote_char or None,
                        strict=True)
    rows: list[list[Optional[str]]] = []
    try:
        for i, rec in enumerate(reader):
            if i == 0 and cfg.has_header:
                header_cols = len(rec)
                continue
            if len(rec) != expected_cols:
                raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                   f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols}")
            rows.append(_normalise(rec, settings.empty_as_null))
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file at line {reader.line_num}: {e}")
    if cfg.has_header and header_cols is None:
        raise FileRejected("FILE_PARSE_ERROR", "header expected but file has no lines")
    if trailer_count is not None and trailer_count != len(rows):
        raise FileRejected("FILE_TRAILER_COUNT_MISMATCH",
                           f"trailer count {trailer_count} != data rows {len(rows)}")
    return ReadResult(rows, header_cols, trailer_count)


def scan_delimited(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> int:
    """Streaming structural check (no rows kept in memory). Returns the data row count."""
    _check_terminator(cfg)
    if cfg.has_trailer:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "streaming scan does not support trailer records yet (Q-02)")
    count = 0
    try:
        with open(path, "r", encoding=settings.file_encoding, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter_for(cfg), quotechar=settings.quote_char or None, strict=True)
            for i, rec in enumerate(reader):
                if i == 0 and cfg.has_header:
                    continue
                if not rec:
                    continue
                if len(rec) != expected_cols:
                    raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                       f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols}")
                count += 1
    except UnicodeDecodeError as e:
        raise FileRejected("FILE_PARSE_ERROR", f"file is not valid {settings.file_encoding}: {e.reason}")
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file: {e}")
    return count


def read_with_pandas(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> ReadResult:
    import pandas as pd  # optional dependency

    try:
        if cfg.src_file_ty in (".xlsx", ".xls"):
            sheet = int(settings.xlsx_sheet) if settings.xlsx_sheet.isdigit() else settings.xlsx_sheet
            df = pd.read_excel(path, sheet_name=sheet, header=None, dtype=str, keep_default_na=False,
                               skiprows=settings.xlsx_header_row + (1 if cfg.has_header else 0))
        else:
            df = pd.read_parquet(path)
            df = df.astype("string").fillna("")
    except Exception as e:  # noqa: BLE001 - any reader failure is a structural failure
        raise FileRejected("FILE_PARSE_ERROR", f"cannot read {cfg.src_file_ty}: {type(e).__name__}")
    if cfg.has_trailer and len(df):
        df = df.iloc[:-1]
    if df.shape[1] != expected_cols and len(df):
        raise FileRejected("FILE_COLUMN_COUNT_MISMATCH", f"{df.shape[1]} columns, expected {expected_cols}")
    rows = [_normalise([str(v) for v in rec], settings.empty_as_null) for rec in df.itertuples(index=False)]
    return ReadResult(rows, None, None)


def read_file(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> ReadResult:
    ft = cfg.src_file_ty.lower()
    if ft not in settings.supported_file_types:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} is not enabled (Q-02)")
    if ft in (".txt", ".csv", ".dat", ".psv", ".tsv"):
        return read_delimited(path, cfg, expected_cols, settings)
    if ft in (".xlsx", ".xls", ".parquet"):
        return read_with_pandas(path, cfg, expected_cols, settings)
    raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} has no reader")


# ============================================================================ staging (D-05, D-62)
@dataclass
class StageResult:
    data_rows: int
    trailer_count: Optional[int]


def stage(conn: psycopg.Connection, settings: "Settings", *, file_path: str, cfg: "FileConfig", btch_id: str,
          load_id: int, src_file_nm: str, loaded_at: datetime, spark=None) -> StageResult:
    """Delete staging rows for btch_id and load the file tagged with load_id."""
    stg_columns = staging_business_columns(conn, cfg.stg_schema_nm, cfg.stg_tblnm)
    table = _ident(cfg.stg_schema_nm, cfg.stg_tblnm)
    if settings.load_engine == "SPARK":
        return _stage_spark(conn, settings, spark, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm,
                            loaded_at)
    result = read_file(file_path, cfg, len(stg_columns), settings)
    cols = [*stg_columns, *STAGING_FRAMEWORK_COLS]
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(table, sql.SQL(", ").join(map(sql.Identifier, cols)))
    try:
        with conn.transaction():
            conn.execute(sql.SQL("DELETE FROM {} WHERE btch_id = %s").format(table), (btch_id,))
            with conn.cursor() as cur, cur.copy(copy_sql) as cp:
                for rec in result.rows:
                    cp.write_row([*rec, btch_id, load_id, src_file_nm, loaded_at])
    except (psycopg.errors.DataError, psycopg.errors.IntegrityError) as e:
        raise FileRejected("FILE_PARSE_ERROR",
                           f"value does not fit staging column types: {sanitize_db_error(str(e.diag.message_primary))}")
    return StageResult(result.data_row_count, result.trailer_count)


def _stage_spark(conn, settings, spark, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm, loaded_at):
    """Large files: streaming structural scan, then Spark JDBC append. A partial append is never promoted
    because promotion filters on Load_ID and checks the staged row count (§10.1)."""
    from pyspark.sql import SparkSession, functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    if cfg.src_file_ty not in (".txt", ".csv") or cfg.has_trailer:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "Spark engine reads delimited files without trailer only")
    if not settings.spark_jdbc_url:
        raise ConfigError("LOAD_ENGINE=SPARK requires SPARK_JDBC_URL")
    rows = scan_delimited(file_path, cfg, len(stg_columns), settings)
    spark = spark or SparkSession.builder.appName("cms-compliance-framework").getOrCreate()
    df = (spark.read.option("header", str(cfg.has_header).lower()).option("sep", delimiter_for(cfg))
          .option("quote", settings.quote_char).option("encoding", settings.file_encoding).option("mode", "FAILFAST")
          .schema(StructType([StructField(c, StringType(), True) for c in stg_columns])).csv(file_path))
    if settings.empty_as_null:
        df = df.select([F.when(F.col(c) == "", None).otherwise(F.col(c)).alias(c) for c in stg_columns])
    df = (df.withColumn("btch_id", F.lit(btch_id)).withColumn("load_id", F.lit(load_id))
          .withColumn("src_file_nm", F.lit(src_file_nm)).withColumn("stg_load_dtts", F.lit(loaded_at)))
    with conn.transaction():
        conn.execute(sql.SQL("DELETE FROM {} WHERE btch_id = %s").format(_ident(cfg.stg_schema_nm, cfg.stg_tblnm)),
                     (btch_id,))
    info = conn.info
    props = {"driver": "org.postgresql.Driver", "stringtype": "unspecified", "user": info.user or "",
             "password": info.password or ""}
    try:
        (df.repartition(settings.spark_write_partitions).write.option("batchsize", settings.spark_batch_size)
         .jdbc(settings.spark_jdbc_url, f"{cfg.stg_schema_nm}.{cfg.stg_tblnm}", mode="append", properties=props))
    except Exception as e:  # noqa: BLE001
        if "invalid input syntax" in str(e) or "out of range" in str(e):
            raise FileRejected("FILE_PARSE_ERROR", "value does not fit staging column types")
        raise
    return StageResult(rows, None)


def restage(conn, store: "ObjectStore", settings: "Settings", cfg: "FileConfig", load: dict, now) -> int:
    """Re-stage an approved reopen file from the S3 archive (D-53)."""
    from .adapters import basename, parse_uri, sha256_file

    bucket, prefix = parse_uri(cfg.src_file_archive_path)
    key = f"{prefix}{basename(load['s3_key'])}"
    if not store.exists(bucket, key):
        raise TechnicalFailure(f"archived object {bucket}/{key} not found")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, basename(key))
        store.download(bucket, key, path)
        if load["file_sha256"] and sha256_file(path) != load["file_sha256"]:
            raise TechnicalFailure(f"archived object {bucket}/{key} does not match the approved file checksum")
        res = stage(conn, settings, file_path=path, cfg=cfg, btch_id=load["btch_id"], load_id=load["load_id"],
                    src_file_nm=basename(load["s3_key"]), loaded_at=now)
    if res.data_rows != load["stg_rcd_cnt"]:
        raise TechnicalFailure(f"re-staged {res.data_rows} rows, expected {load['stg_rcd_cnt']}")
    return res.data_rows


# ============================================================================ promotion (D-01, §10.2)
@dataclass
class PromotionResult:
    disabled_cnt: int
    appended_cnt: int


def staged_row_count(conn, cfg: "FileConfig", btch_id: str, load_id: int) -> int:
    return int(conn.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE btch_id=%s AND load_id=%s")
                            .format(_ident(cfg.stg_schema_nm, cfg.stg_tblnm)), (btch_id, load_id)).fetchone()["n"])


def swap(conn, cfg: "FileConfig", btch_id: str, load_id: int, expected_rows: int, now: datetime) -> PromotionResult:
    """Must run inside the caller's open transaction; the caller holds the batch advisory lock."""
    cols = core_insert_columns(conn, cfg)
    core, stg = _ident(cfg.core_schema_nm, cfg.core_tblnm), _ident(cfg.stg_schema_nm, cfg.stg_tblnm)
    col_sql = sql.SQL("").join(sql.SQL("{}, ").format(sql.Identifier(c)) for c in cols)
    disabled = conn.execute(sql.SQL("UPDATE {} SET current_ind = 0, end_dtts = %s WHERE btch_id = %s "
                                    "AND current_ind = 1").format(core), (now, btch_id)).rowcount
    appended = conn.execute(sql.SQL(
        "INSERT INTO {core} ({cols}btch_id, load_id, current_ind, load_dtts) "
        "SELECT {cols}btch_id, load_id, 1, %s FROM {stg} WHERE btch_id = %s AND load_id = %s").format(
            core=core, cols=col_sql, stg=stg), (now, btch_id, load_id)).rowcount
    if appended != expected_rows:
        raise RowCountMismatch(f"appended {appended} rows for load {load_id}, staged {expected_rows}")
    return PromotionResult(disabled, appended)
