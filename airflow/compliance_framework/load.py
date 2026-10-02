"""Staging load and core promotion on Teradata (design §9.4, §10)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

from . import db
from .common import FileRejected, RowCountMismatch
from .db import Connection

if TYPE_CHECKING:
    from .config import FileConfig
    from .settings import Settings

STAGING_FRAMEWORK_COLS = ("btch_id", "load_id", "src_file_nm", "stg_load_dtts")
CORE_FRAMEWORK_COLS = ("btch_id", "load_id", "current_ind", "load_dtts", "end_dtts")


@dataclass(frozen=True)
class Column:
    name: str
    auto: bool


def columns(conn: Connection, schema: str, table: str) -> list[Column]:
    """Columns of a table in definition order (lower-case names); auto = identity column."""
    rows = conn.execute(
        """SELECT ColumnName AS column_name, IdColType AS id_col_type FROM DBC.ColumnsV
            WHERE UPPER(DatabaseName) = UPPER(%s) AND UPPER(TableName) = UPPER(%s) ORDER BY ColumnId""",
        (schema, table)).fetchall()
    return [Column(r["column_name"].strip().lower(), bool((r["id_col_type"] or "").strip())) for r in rows]


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
    """Staging business columns that exist in core, minus identity columns (D-61)."""
    core = _table_columns(conn, cfg.core_schema_nm, cfg.table_nm, CORE_FRAMEWORK_COLS, "core")
    usable = {c.name for c in core if not c.auto and c.name not in CORE_FRAMEWORK_COLS}
    return [c for c in staging_business_columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm) if c in usable]


def _table(schema: str, table: str) -> str:
    return db.qualified(schema, table)


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


def read_delimited(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings") -> Iterator[list]:
    """Rows yielded one at a time; LF and CRLF line endings are both accepted, trailing blank lines ignored."""
    delim = delimiter_for(cfg)
    content_cnt, last = 0, ""
    try:
        for n, line in enumerate(_file_lines(path, settings.file_encoding), 1):
            if line.strip() != "":
                content_cnt, last = n, line
    except UnicodeDecodeError as e:
        raise FileRejected("FILE_PARSE_ERROR", f"file is not valid {settings.file_encoding}: {e.reason}")
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
    if ft in (".txt", ".csv", ".dat", ".psv", ".tsv"):
        return read_delimited(path, cfg, expected_cols, settings)
    if ft in (".xlsx", ".xls", ".parquet"):
        return read_with_pandas(path, cfg, expected_cols, settings)
    raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} has no reader")


def stage(conn: Connection, settings: "Settings", *, file_path: str, cfg: "FileConfig", btch_id: str,
          load_id: int, src_file_nm: str, loaded_at: datetime) -> int:
    """Replace the staging rows of btch_id with the file, in batched inserts; returns the row count."""
    stg_columns = staging_business_columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm)
    table = _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    rows = read_file(file_path, cfg, len(stg_columns), settings)
    cols = [*stg_columns, *STAGING_FRAMEWORK_COLS]
    insert = (f"INSERT INTO {table} ({', '.join(db.ident(c) for c in cols)}) "
              f"VALUES ({', '.join(['%s'] * len(cols))})")
    try:
        with conn.transaction():
            conn.execute(f"DELETE FROM {table} WHERE btch_id = %s", (btch_id,))
            count = conn.executemany(insert, ([*rec, btch_id, load_id, src_file_nm, loaded_at] for rec in rows))
    except FileRejected:
        raise
    except Exception as e:  # noqa: BLE001 - only data-conversion errors are a file problem
        if not db.is_data_error(e):
            raise
        raise FileRejected("FILE_PARSE_ERROR",
                           f"value does not fit staging column types: {sanitize_db_error(str(e).splitlines()[0])}")
    return count


@dataclass
class PromotionResult:
    disabled_cnt: int
    appended_cnt: int


def swap(conn, cfg: "FileConfig", btch_id: str, load_id: int, expected_rows: int, now: datetime) -> PromotionResult:
    """Must run inside the caller's open transaction; the caller holds the batch lock."""
    cols = core_insert_columns(conn, cfg)
    core, stg = _table(cfg.core_schema_nm, cfg.table_nm), _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    col_sql = "".join(f"{db.ident(c)}, " for c in cols)
    disabled = conn.execute(f"UPDATE {core} SET current_ind = 0, end_dtts = %s WHERE btch_id = %s "
                            "AND current_ind = 1", (now, btch_id)).rowcount
    appended = conn.execute(
        f"INSERT INTO {core} ({col_sql}btch_id, load_id, current_ind, load_dtts) "
        f"SELECT {col_sql}btch_id, load_id, 1, CAST(%s AS TIMESTAMP(6) WITH TIME ZONE) FROM {stg} "
        "WHERE btch_id = %s AND load_id = %s", (now, btch_id, load_id)).rowcount
    if appended != expected_rows:
        raise RowCountMismatch(f"appended {appended} rows for load {load_id}, staged {expected_rows}")
    return PromotionResult(disabled, appended)
