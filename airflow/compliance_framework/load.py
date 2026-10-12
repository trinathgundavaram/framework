"""Staging load and core promotion on Teradata (design §9.4, §10)."""
from __future__ import annotations

import codecs
import csv
import logging
import re
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

from . import db
from .common import ConfigError, FileRejected, RowCountMismatch
from .db import Connection
from .layout import BATCH_COLUMN, Column, Layout, LoadContext, build_layout, column_mapping

if TYPE_CHECKING:
    from .config import FileConfig
    from .settings import Settings

log = logging.getLogger(__name__)

def columns(conn: Connection, schema: str, table: str) -> list[Column]:
    """Columns of a table in definition order (lower-case names); auto = identity, text = character column."""
    rows = conn.execute(
        """SELECT ColumnName AS column_name, IdColType AS id_col_type, ColumnType AS column_type FROM DBC.ColumnsV
            WHERE UPPER(DatabaseName) = UPPER(%s) AND UPPER(TableName) = UPPER(%s) ORDER BY ColumnId""",
        (schema, table)).fetchall()
    return [Column(r["column_name"].strip().lower(), bool((r["id_col_type"] or "").strip()),
                   (r["column_type"] or "").strip().upper() in ("CV", "CF", "CO")) for r in rows]


def file_layout(conn, cfg: "FileConfig", target_columns) -> Layout:
    """The staging / core layout of a file config; LookupError when a table or a required column is missing."""
    return build_layout(f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}",
                        columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm),
                        columns(conn, cfg.core_schema_nm, cfg.core_table_nm), column_mapping(cfg, target_columns),
                        f"{cfg.stg_schema_nm}.{cfg.stg_table_nm}", f"{cfg.core_schema_nm}.{cfg.core_table_nm}")


def _table(schema: str, table: str) -> str:
    return db.qualified(schema, table)


DELIMITED_FILE_TYPES = (".txt", ".csv", ".dat", ".psv", ".tsv")
Rows = Iterable[list[Optional[str]]]


def delimiter_for(cfg: "FileConfig") -> str:
    """The column delimiter of a file config: the character itself in Delmtr_Cd (\\t for a tab); a comma when empty."""
    d = cfg.delmtr_cd
    if d is None or d == "":
        return ","
    if d == "\t" or d.strip().lower() in ("\\t", "/t"):
        return "\t"
    d = d.strip() or d[:1]
    if len(d) != 1:
        raise ConfigError(f"Delmtr_Cd {cfg.delmtr_cd!r} of Cfg_ID {cfg.cfg_id} must be the delimiter character itself, "
                          "for example | or , or * (\\t for a tab)")
    return d


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


def raw_line(path: str, line_no: Optional[int], encoding: str) -> Optional[str]:
    """Line `line_no` (1-based) of a file as text; bytes that are not valid in `encoding` show as U+FFFD."""
    if not line_no:
        return None
    with open(path, "rb") as f:
        for n, line in enumerate(f, 1):
            if n == line_no:
                return line.rstrip(b"\r\n").decode(encoding, "replace")
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


def read_delimited(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings",
                   drop_first: bool = False) -> Iterator[list]:
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
            if len(rec) != expected_cols + drop_first:
                raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                   f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols + drop_first}")
            data_rows += 1
            yield _normalise(rec[1:] if drop_first else rec, settings.empty_as_null)
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file at line {reader.line_num}: {e}")
    if cfg.has_header and not seen_header:
        raise FileRejected("FILE_PARSE_ERROR", "header expected but file has no lines")
    if trailer_count is not None and trailer_count != data_rows:
        raise FileRejected("FILE_TRAILER_COUNT_MISMATCH",
                           f"trailer count {trailer_count} != data rows {data_rows}")


def read_with_pandas(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings",
                     drop_first: bool = False) -> list[list]:
    import pandas as pd

    try:
        if cfg.src_file_ty in (".xlsx", ".xls"):
            sheet = int(settings.xlsx_sheet) if settings.xlsx_sheet.isdigit() else settings.xlsx_sheet
            df = pd.read_excel(path, sheet_name=sheet, header=None, dtype=str, keep_default_na=False,
                               skiprows=settings.xlsx_header_row + (1 if cfg.has_header else 0))
        else:
            df = pd.read_parquet(path)
            df = df.astype("string").fillna("")
    except Exception as e:  # noqa: BLE001
        raise FileRejected("FILE_PARSE_ERROR", f"cannot read {cfg.src_file_ty}: {type(e).__name__}")
    if cfg.has_trailer and len(df):
        df = df.iloc[:-1]
    if df.shape[1] != expected_cols + drop_first and len(df):
        raise FileRejected("FILE_COLUMN_COUNT_MISMATCH", f"{df.shape[1]} columns, expected {expected_cols + drop_first}")
    if drop_first:
        df = df.iloc[:, 1:]
    return [_normalise([str(v) for v in rec], settings.empty_as_null) for rec in df.itertuples(index=False)]


def read_file(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings", drop_first: bool = False) -> Rows:
    """The data rows of a file; `drop_first` = every line starts with a record type field that is not loaded."""
    ft = cfg.src_file_ty
    if ft not in settings.supported_file_types:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} is not enabled (Q-02)")
    if ft in DELIMITED_FILE_TYPES:
        return read_delimited(path, cfg, expected_cols, settings, drop_first)
    if ft in (".xlsx", ".xls", ".parquet"):
        return read_with_pandas(path, cfg, expected_cols, settings, drop_first)
    raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} has no reader")


def next_sequence(conn, cfg: "FileConfig", layout: Layout) -> int:
    """The next value of the ROW_SEQUENCE column: one above the highest in the staging and core tables."""
    highest = 0
    for kind, table in (("stage", _table(cfg.stg_schema_nm, cfg.stg_table_nm)),
                        ("core", _table(cfg.core_schema_nm, cfg.core_table_nm))):
        col = layout.column("ROW_SEQUENCE", kind)
        if col:
            row = conn.execute(f"SELECT MAX(CAST({db.ident(col.name)} AS BIGINT)) AS m FROM {table}").fetchone()
            highest = max(highest, int(row["m"] or 0))
    return highest + 1


def _clean_stage(conn, table: str, cfg: "FileConfig", layout: Layout, ctx: LoadContext) -> None:
    if cfg.stg_cleanup_cd == "RUN_TYPE":
        col = layout.column("RUN_TYPE")
        if col is None:
            raise ConfigError(f"{layout.label}: Stg_Cleanup_Cd RUN_TYPE needs a staging column filled with RUN_TYPE")
        conn.execute(f"DELETE FROM {table} WHERE {db.ident(col.name)} = %s", (ctx.value("RUN_TYPE", col.text),))
    else:
        conn.execute(f"DELETE FROM {table} WHERE {db.ident(BATCH_COLUMN)} = %s", (ctx.btch_id,))


def stage(conn: Connection, settings: "Settings", *, file_path: str, cfg: "FileConfig", layout: Layout,
          ctx: LoadContext, drop_first: bool = False) -> int:
    """Clear the staging rows (of the batch, or of its run type) and load the file; returns the row count."""
    table = _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    records = read_file(file_path, cfg, len(layout.file_cols), settings, drop_first)
    cols = [c.name for c in layout.stage]
    insert = (f"INSERT INTO {table} ({', '.join(db.ident(c) for c in cols)}) "
              f"VALUES ({', '.join(['%s'] * len(cols))})")
    try:
        with conn.transaction():
            start = next_sequence(conn, cfg, layout)
            _clean_stage(conn, table, cfg, layout, ctx)
            count = conn.executemany(insert, layout.stage_rows(records, ctx, start))
    except FileRejected:
        raise
    except Exception as e:  # noqa: BLE001
        if not db.is_data_error(e):
            raise
        where = row_position(file_path, cfg, settings, _rejected_row(conn, insert, e))
        raise FileRejected("FILE_PARSE_ERROR", f"value does not fit staging column types{' at ' + where if where else ''}: "
                                               f"{sanitize_db_error(str(e).splitlines()[0])}")
    return count


def _rejected_row(conn: Connection, insert: str, e: Exception) -> Optional[int]:
    """1-based data row the database refused, found by re-sending parts of the failed batch."""
    rows = getattr(e, "failed_rows", None)
    if not rows:
        return None
    try:
        at = conn.first_rejected(insert, rows)
    except Exception:  # noqa: BLE001
        log.warning("the rejected row could not be located", exc_info=True)
        return None
    return None if at is None else e.failed_offset + at + 1


@dataclass
class PromotionResult:
    disabled_cnt: int
    appended_cnt: int


def swap(conn, cfg: "FileConfig", layout: Layout, ctx: LoadContext, expected_rows: int) -> PromotionResult:
    """Disable the batch's current core rows and append the staged ones; runs inside the caller's transaction."""
    core, stg = _table(cfg.core_schema_nm, cfg.core_table_nm), _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    flag, end = layout.column("CURRENT_FLAG", "core"), layout.column("END_TIMESTAMP", "core")
    on, off = ("'1'", "'0'") if flag.text else ("1", "0")
    set_end, params = (f", {db.ident(end.name)} = %s", [ctx.value("LOAD_TIMESTAMP", end.text)]) if end else ("", [])
    disabled = conn.execute(f"UPDATE {core} SET {db.ident(flag.name)} = {off}{set_end} WHERE {db.ident(BATCH_COLUMN)} = %s "
                            f"AND {db.ident(flag.name)} = {on}", (*params, ctx.btch_id)).rowcount
    names, exprs, values = [], [], []
    for c, kind, _ in layout.core_plan():
        names.append(db.ident(c.name))
        if kind == "copy":
            exprs.append(db.ident(c.name))
        elif kind == "flag":
            exprs.append(on)
        else:
            exprs.append("CAST(%s AS VARCHAR(50))" if c.text else "CAST(%s AS TIMESTAMP(6) WITH TIME ZONE)")
            values.append(ctx.value("LOAD_TIMESTAMP", c.text))
    where = f"{db.ident(BATCH_COLUMN)} = %s"
    values.append(ctx.btch_id)
    load_col = layout.column("LOAD_ID", "stage")
    if load_col:
        where += f" AND {db.ident(load_col.name)} = %s"
        values.append(ctx.value("LOAD_ID", load_col.text))
    appended = conn.execute(f"INSERT INTO {core} ({', '.join(names)}) SELECT {', '.join(exprs)} FROM {stg} WHERE {where}",
                            values).rowcount
    if appended != expected_rows:
        raise RowCountMismatch(f"appended {appended} rows for load {ctx.load_id}, staged {expected_rows}")
    return PromotionResult(disabled, appended)
