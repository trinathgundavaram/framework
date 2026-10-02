"""Staging load and core promotion (design §9.4, §10)."""
from __future__ import annotations

import codecs
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

import psycopg
from psycopg import sql

from .common import ConfigError, FileRejected, RowCountMismatch

if TYPE_CHECKING:
    from .config import FileConfig
    from .settings import Settings

STAGING_FRAMEWORK_COLS = ("btch_id", "load_id", "src_file_nm", "stg_load_dtts")
CORE_FRAMEWORK_COLS = ("btch_id", "load_id", "current_ind", "load_dtts", "end_dtts")


@dataclass(frozen=True)
class Column:
    name: str
    auto: bool


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
    core = _table_columns(conn, cfg.core_schema_nm, cfg.table_nm, CORE_FRAMEWORK_COLS, "core")
    usable = {c.name for c in core if not c.auto and c.name not in CORE_FRAMEWORK_COLS}
    return [c for c in staging_business_columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm) if c in usable]


def _ident(schema: str, table: str) -> sql.Identifier:
    return sql.Identifier(schema.lower(), table.lower())


DELIMITED_FILE_TYPES = (".txt", ".csv", ".dat", ".psv", ".tsv")
_DELIMS = {"TAB": "\t", "\\T": "\t", "PIPE": "|", "COMMA": ",", "SEMICOLON": ";"}
Rows = Iterable[list[Optional[str]]]


def delimiter_for(cfg: "FileConfig") -> str:
    d = cfg.delmtr_cd
    return _DELIMS.get(d.upper(), d) if d else ","


def sanitize_db_error(msg: str) -> str:
    """Remove quoted values from database error text so no file content reaches audit logs."""
    return re.sub(r'"[^"]*"', '"***"', msg or "")[:500]


def _normalise(values: list[str], empty_as_null: bool) -> list[Optional[str]]:
    return [None if (empty_as_null and v == "") else v for v in values]


def _file_lines(path: str, encoding: str) -> Iterator[str]:
    """The lines `str.splitlines(keepends=True)` finds in the file, read incrementally."""
    with open(path, "r", encoding=encoding, newline="") as f:
        for line in f:
            yield from line.splitlines(keepends=True)


def _lf_lines(parts: Iterable[str]) -> Iterator[str]:
    """Re-split text on '\\n' only - what iterating an io.StringIO of the same text yields."""
    buf = ""
    for part in parts:
        buf += part
        if "\n" in buf:
            *done, buf = buf.split("\n")
            for d in done:
                yield d + "\n"
    if buf:
        yield buf


def _undecodable_line(path: str, encoding: str) -> Optional[int]:
    """Line number of the first bytes that are not valid in `encoding`."""
    decoder = codecs.getincrementaldecoder(encoding)()
    line = 1
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            state = decoder.getstate()
            try:
                line += decoder.decode(chunk, final=not chunk).count("\n")
            except UnicodeDecodeError:
                decoder.setstate(state)
                try:
                    for i in range(len(chunk)):
                        line += decoder.decode(chunk[i:i + 1]).count("\n")
                except UnicodeDecodeError:
                    pass
                return line
            if not chunk:
                return None


def _not_decodable(path: str, encoding: str, e: UnicodeDecodeError) -> FileRejected:
    line = _undecodable_line(path, encoding)
    where = f" at line {line}" if line else ""
    return FileRejected("FILE_PARSE_ERROR", f"file is not valid {encoding}{where}: {e.reason}")


def row_position(path: str, cfg: "FileConfig", settings: "Settings", row_no: Optional[int]) -> str:
    """Where data row `row_no` (1-based) sits in the file: 'line N' for delimited files, else 'data row N'."""
    if not row_no:
        return ""
    if cfg.src_file_ty in DELIMITED_FILE_TYPES:
        reader = csv.reader(_lf_lines(_file_lines(path, settings.file_encoding)), delimiter=delimiter_for(cfg),
                            quotechar=settings.quote_char or None, strict=True)
        target = row_no + (1 if cfg.has_header else 0)
        for i, _ in enumerate(reader, 1):
            if i == target:
                return f"line {reader.line_num}"
    return f"data row {row_no}"


def read_delimited(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> Iterator[list]:
    """Rows yielded one at a time; LF and CRLF line endings are both accepted, trailing blank lines ignored."""
    delim = delimiter_for(cfg)
    content_cnt, last = 0, ""
    try:
        for n, line in enumerate(_file_lines(path, settings.file_encoding), 1):
            if line.strip() != "":
                content_cnt, last = n, line
    except UnicodeDecodeError as e:
        raise _not_decodable(path, settings.file_encoding, e)
    trailer_count = None
    if cfg.has_trailer:
        if not content_cnt:
            raise FileRejected("FILE_PARSE_ERROR", "trailer expected but file is empty")
        content_cnt -= 1
        if settings.trailer_count_check:
            m = re.search(settings.trailer_count_regex, last)
            if not m:
                raise FileRejected("FILE_TRAILER_COUNT_MISMATCH", "trailer record count not found")
            trailer_count = int(m.group(1))
    reader = csv.reader(_lf_lines(islice(_file_lines(path, settings.file_encoding), content_cnt)),
                        delimiter=delim, quotechar=settings.quote_char or None, strict=True)
    data_rows = 0
    seen_header = False
    try:
        for i, rec in enumerate(reader):
            if i == 0 and cfg.has_header:
                seen_header = True
                continue
            if len(rec) != expected_cols:
                raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                   f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols}")
            data_rows += 1
            yield _normalise(rec, settings.empty_as_null)
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file at line {reader.line_num}: {e}")
    if cfg.has_header and not seen_header:
        raise FileRejected("FILE_PARSE_ERROR", "header expected but file has no lines")
    if trailer_count is not None and trailer_count != data_rows:
        raise FileRejected("FILE_TRAILER_COUNT_MISMATCH",
                           f"trailer count {trailer_count} != data rows {data_rows}")


def scan_delimited(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> int:
    """Streaming structural check (no rows kept in memory)."""
    if cfg.has_trailer:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "streaming scan does not support trailer records yet (Q-02)")
    count = 0
    try:
        with open(path, "r", encoding=settings.file_encoding, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter_for(cfg), quotechar=settings.quote_char or None, strict=True)
            for i, rec in enumerate(reader):
                if not rec or (i == 0 and cfg.has_header):
                    continue
                if len(rec) != expected_cols:
                    raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                       f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols}")
                count += 1
    except UnicodeDecodeError as e:
        raise _not_decodable(path, settings.file_encoding, e)
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file at line {reader.line_num}: {e}")
    return count


def read_with_pandas(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> list[list]:
    import pandas as pd

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
    return [_normalise([str(v) for v in rec], settings.empty_as_null) for rec in df.itertuples(index=False)]


def read_file(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> Rows:
    ft = cfg.src_file_ty
    if ft not in settings.supported_file_types:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} is not enabled (Q-02)")
    if ft in DELIMITED_FILE_TYPES:
        return read_delimited(path, cfg, expected_cols, settings)
    if ft in (".xlsx", ".xls", ".parquet"):
        return read_with_pandas(path, cfg, expected_cols, settings)
    raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} has no reader")


def stage(conn: psycopg.Connection, settings: "Settings", *, file_path: str, cfg: "FileConfig", btch_id: str,
          load_id: int, src_file_nm: str, loaded_at: datetime, spark=None) -> int:
    """Replace the staging rows of btch_id with the file, streamed into COPY; returns the row count."""
    stg_columns = staging_business_columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm)
    table = _ident(cfg.stg_schema_nm, cfg.stg_table_nm)
    if settings.load_engine == "SPARK":
        return _stage_spark(conn, settings, spark, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm,
                            loaded_at)
    rows = read_file(file_path, cfg, len(stg_columns), settings)
    cols = [*stg_columns, *STAGING_FRAMEWORK_COLS]
    count = 0
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(table, sql.SQL(", ").join(map(sql.Identifier, cols)))
    try:
        with conn.transaction():
            conn.execute(sql.SQL("DELETE FROM {} WHERE btch_id = %s").format(table), (btch_id,))
            with conn.cursor() as cur, cur.copy(copy_sql) as cp:
                for rec in rows:
                    cp.write_row([*rec, btch_id, load_id, src_file_nm, loaded_at])
                    count += 1
    except (psycopg.errors.DataError, psycopg.errors.IntegrityError) as e:
        where = _rejected_at(e, count, file_path, cfg, settings)
        raise FileRejected("FILE_PARSE_ERROR", f"value does not fit staging column types{where}: "
                                               f"{sanitize_db_error(e.diag.message_primary or str(e))}")
    return count


_COPY_CONTEXT = re.compile(r", line (\d+)(?:, column ([^:\s]+))?")


def _rejected_at(e: psycopg.Error, written: int, file_path: str, cfg: "FileConfig", settings: "Settings") -> str:
    """' at line N (column c)' for the row the database refused; '' when it cannot be told."""
    m = _COPY_CONTEXT.search(e.diag.context or "")
    row_no = int(m.group(1)) if m else written + 1 if e.diag.sqlstate is None else None
    where = row_position(file_path, cfg, settings, row_no)
    if not where:
        return ""
    return f" at {where}" + (f" (column {m.group(2)})" if m and m.group(2) else "")


def _stage_spark(conn, settings, spark, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm, loaded_at):
    """Spark engine: streaming scan, then JDBC append."""
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
        conn.execute(sql.SQL("DELETE FROM {} WHERE btch_id = %s").format(_ident(cfg.stg_schema_nm, cfg.stg_table_nm)),
                     (btch_id,))
    info = conn.info
    props = {"driver": "org.postgresql.Driver", "stringtype": "unspecified", "user": info.user or "",
             "password": info.password or ""}
    try:
        (df.repartition(settings.spark_write_partitions).write.option("batchsize", settings.spark_batch_size)
         .jdbc(settings.spark_jdbc_url, f"{cfg.stg_schema_nm}.{cfg.stg_table_nm}", mode="append", properties=props))
    except Exception as e:  # noqa: BLE001
        if "invalid input syntax" in str(e) or "out of range" in str(e):
            raise FileRejected("FILE_PARSE_ERROR", "value does not fit staging column types")
        raise
    return rows


@dataclass
class PromotionResult:
    disabled_cnt: int
    appended_cnt: int


def swap(conn, cfg: "FileConfig", btch_id: str, load_id: int, expected_rows: int, now: datetime) -> PromotionResult:
    """Must run inside the caller's open transaction; the caller holds the batch advisory lock."""
    cols = core_insert_columns(conn, cfg)
    core, stg = _ident(cfg.core_schema_nm, cfg.table_nm), _ident(cfg.stg_schema_nm, cfg.stg_table_nm)
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
