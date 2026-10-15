"""Staging load and core promotion on Teradata (design §9.4, §10)."""
from __future__ import annotations

import codecs
import csv
import os
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

from . import db
from .common import ConfigError, FileRejected, RowCountMismatch
from .db import Connection
from .filecheck_options import CheckOptions, trailer_problems
from .layout import BATCH_COLUMN, PLACEHOLDER, Column, Layout, LoadContext, build_layout, column_mapping, sql_body

if TYPE_CHECKING:
    from .config import FileConfig
    from .settings import Settings

log = logging.getLogger(__name__)

def columns(conn: Connection, schema: str, table: str) -> list[Column]:
    """Columns of a table in definition order (lower-case names); auto = identity, text = character column."""
    rows = conn.execute(
        """SELECT ColumnName AS column_name, IdColType AS id_col_type, ColumnType AS column_type,
                  ColumnLength AS column_length, CharType AS char_type, Nullable AS nullable FROM DBC.ColumnsV
            WHERE UPPER(DatabaseName) = UPPER(%s) AND UPPER(TableName) = UPPER(%s) ORDER BY ColumnId""",
        (schema, table)).fetchall()
    out = []
    for r in rows:
        kind = (r["column_type"] or "").strip().upper()
        length = None
        if kind in ("CV", "CF") and r["column_length"]:
            length = int(r["column_length"]) // (2 if int(r["char_type"] or 1) == 2 else 1)
        out.append(Column(r["column_name"].strip().lower(), bool((r["id_col_type"] or "").strip()),
                          kind in ("CV", "CF", "CO"), length, kind in ("I", "I1", "I2", "I8", "D", "F", "N"),
                          (r["nullable"] or "Y").strip().upper() != "N",
                          {"DA": "DATE", "TS": "TIMESTAMP", "SZ": "TIMESTAMP_TZ"}.get(kind)))
    return out


def header_columns(cfg: "FileConfig", opts) -> tuple:
    """The file's column names from its HEADER_NAMES check row; () when it has none."""
    if opts is None or not opts.header:
        return ()
    try:
        delim = delimiter_for(cfg)
    except ConfigError:
        delim = ","
    names = [v.strip() for v in opts.header.split(delim if delim in opts.header else ",")]
    if opts.record_types and names[0] == opts.record_types[0]:
        names = names[1:]
    return tuple(names)


def file_layout(conn, cfg: "FileConfig", target_columns, opts=None) -> Layout:
    """The staging / core layout of a file config; LookupError when a table or a required column is missing.

    `opts` = the file's check options: a TARGET_COLUMNS row is the staging column of each field in file order, a
    HEADER_NAMES row the list of columns the file has, a COLUMN_COUNT row the number of its fields; staging columns
    the file does not fill are loaded as NULL.
    """
    count = None
    if opts is not None and opts.column_count:
        count = opts.column_count - (1 if opts.record_types else 0)
    return build_layout(f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}",
                        columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm),
                        columns(conn, cfg.core_schema_nm, cfg.core_table_nm), column_mapping(cfg, target_columns),
                        f"{cfg.stg_schema_nm}.{cfg.stg_table_nm}", f"{cfg.core_schema_nm}.{cfg.core_table_nm}",
                        header_columns(cfg, opts), count, opts.target_columns if opts is not None else ())


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
                   opts: Optional[CheckOptions] = None) -> Iterator[list]:
    """The data rows of a delimited file, in one pass; LF and CRLF line endings are both accepted.

    The header line is skipped (never counted or compared). With a trailer, the last non-blank line is the
    trailer: it is checked against the file's TRAILER and RECORD_TYPE rows after the last data row. Blank lines
    at the end are ignored; a blank line between records rejects the file.
    """
    opts = opts or CheckOptions()
    drop_first = opts.record_types is not None
    width = expected_cols + drop_first
    empty_as_null = settings.empty_as_null
    reader = csv.reader(_lf_lines(_file_lines(path, settings.file_encoding)), delimiter=delimiter_for(cfg),
                        quotechar=settings.quote_char or None, strict=True)
    skip_header = cfg.has_header
    held = None
    blank_at = None
    data_rows = lines = 0
    try:
        for rec in reader:
            if not rec or (len(rec) == 1 and not rec[0].strip()):
                blank_at = blank_at or reader.line_num
                continue
            if blank_at:
                raise FileRejected("FILE_PARSE_ERROR", f"blank line inside the file at line {blank_at}")
            lines += 1
            if skip_header:
                skip_header = False
                continue
            if held is not None:
                line_no, row = held
                if len(row) != width:
                    raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                       f"line {line_no}: {len(row)} columns, expected {width}")
                if drop_first:
                    row = row[1:]
                yield [None if v == "" else v for v in row] if empty_as_null else row
                data_rows += 1
            held = (reader.line_num, rec)
    except UnicodeDecodeError as e:
        raise _not_decodable(path, settings.file_encoding, e)
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file at line {reader.line_num}: {e}")
    if cfg.has_header and lines == 0:
        raise FileRejected("FILE_PARSE_ERROR", "header expected but file has no lines")
    if cfg.has_trailer:
        if held is None:
            raise FileRejected("FILE_PARSE_ERROR", "trailer expected but not found")
        problems = trailer_problems(held[1], held[0], os.path.basename(path), opts, settings.filename_case_sensitive,
                                    data_rows, lines)
        if problems:
            counts = [m for _, m in problems if "trailer count" in m]
            raise FileRejected("FILE_TRAILER_COUNT_MISMATCH" if counts else "FILE_PARSE_ERROR", (counts or [problems[0][1]])[0])
    elif held is not None:
        line_no, row = held
        if len(row) != width:
            raise FileRejected("FILE_COLUMN_COUNT_MISMATCH", f"line {line_no}: {len(row)} columns, expected {width}")
        if drop_first:
            row = row[1:]
        yield [None if v == "" else v for v in row] if empty_as_null else row


def read_file(path: str, cfg: "FileConfig", expected_cols: int, settings: "Settings",
              opts: Optional[CheckOptions] = None) -> Rows:
    """The data rows of a file; only delimited files are read (SUPPORTED_FILE_TYPES)."""
    ft = cfg.src_file_ty
    if ft not in settings.supported_file_types or ft not in DELIMITED_FILE_TYPES:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} is not supported")
    return read_delimited(path, cfg, expected_cols, settings, opts)


def sql_values(conn, layout: Layout, ctx: LoadContext) -> dict:
    """{column: value} of the staging columns filled by a statement (Value_Cd SQL); each statement runs once per load."""
    out, done, params = {}, {}, ctx.sql_params()
    for c in layout.stage:
        if layout.mapping.get(c.name) != "SQL":
            continue
        text = layout.texts[c.name]
        if text not in done:
            what = f"{layout.label}: Value_Txt of column {c.name}"
            body = sql_body(what, text)
            names = [n.lower() for n in PLACEHOLDER.findall(body)]
            try:
                rows = conn.execute_qmark(PLACEHOLDER.sub("?", body), [params[n] for n in names]).fetchall()
            except Exception as e:  # noqa: BLE001
                raise ConfigError(f"{what} failed: {db.error_line(e)}") from None
            if len(rows) != 1 or len(rows[0]) != 1:
                raise ConfigError(f"{what} must return one row with one column (it returned {len(rows)} row(s))")
            done[text] = next(iter(rows[0].values()))
        out[c.name] = done[text]
    return out


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
    """Remove the staging rows of the file's run type; of its batch when the table has no run type column."""
    col = layout.column("RUN_TYPE")
    if col:
        conn.execute(f"DELETE FROM {table} WHERE {db.ident(col.name)} = %s", (ctx.value("RUN_TYPE", col.text),))
    else:
        conn.execute(f"DELETE FROM {table} WHERE {db.ident(BATCH_COLUMN)} = %s", (ctx.btch_id,))


def stage(conn: Connection, settings: "Settings", *, file_path: str, cfg: "FileConfig", layout: Layout,
          ctx: LoadContext, opts: Optional[CheckOptions] = None) -> int:
    """Clear the staging rows (of the batch, or of its run type) and load the file; returns the row count."""
    table = _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    records = read_file(file_path, cfg, len(layout.file_cols), settings, opts)
    cols = [c.name for c in layout.stage]
    names = ", ".join(db.ident(c) for c in cols)
    resolved = sql_values(conn, layout, ctx)
    if settings.load_engine == "FASTLOAD":
        return _stage_fastload(conn, settings, cfg, layout, ctx, records, resolved, table, names, len(cols))
    insert = f"INSERT INTO {table} ({names}) VALUES ({', '.join(['%s'] * len(cols))})"
    try:
        with conn.transaction():
            start = next_sequence(conn, cfg, layout)
            _clean_stage(conn, table, cfg, layout, ctx)
            count = conn.executemany(insert, layout.stage_rows(records, ctx, start, resolved),
                                     settings.insert_batch_rows, settings.insert_batch_bytes)
    except FileRejected:
        raise
    except Exception as e:  # noqa: BLE001
        if not db.is_data_error(e):
            raise
        where = row_position(file_path, cfg, settings, _rejected_row(conn, insert, e))
        raise FileRejected("FILE_PARSE_ERROR", f"value does not fit staging column types{' at ' + where if where else ''}: "
                                               f"{sanitize_db_error(db.error_line(e))}")
    return count


def stage_rows(conn: Connection, settings: "Settings", *, cfg, layout: Layout, ctx: LoadContext, records: Rows) -> int:
    """Clear the staging rows (of the batch, or of its run type) and insert `records` (rows already read from a
    source); returns the row count. A value that does not fit is reported with its row number."""
    table = _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    cols = [c.name for c in layout.stage]
    insert = f"INSERT INTO {table} ({', '.join(db.ident(c) for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    resolved = sql_values(conn, layout, ctx)
    try:
        with conn.transaction():
            start = next_sequence(conn, cfg, layout)
            _clean_stage(conn, table, cfg, layout, ctx)
            return conn.executemany(insert, layout.stage_rows(records, ctx, start, resolved),
                                    settings.insert_batch_rows, settings.insert_batch_bytes)
    except FileRejected:
        raise
    except Exception as e:  # noqa: BLE001
        if not db.is_data_error(e):
            raise
        row = _rejected_row(conn, insert, e)
        raise FileRejected("SOURCE_DATA_REJECTED", f"value does not fit staging column types{f' at data row {row}' if row else ''}: "
                                                   f"{sanitize_db_error(db.error_line(e))}")


def _cast(c: Column, value) -> tuple[str, list]:
    """A select-list expression for a value that is the same on every row, typed for its column."""
    if value is None:
        return "NULL", []
    if c.text:
        return f"CAST(? AS VARCHAR({c.length or 4000}))", [str(value)]
    kind = {"DATE": "DATE", "TIMESTAMP": "TIMESTAMP(0)", "TIMESTAMP_TZ": "TIMESTAMP(0) WITH TIME ZONE"}.get(c.temporal or "")
    if kind:
        return f"CAST(? AS {kind})", [value]
    return ("CAST(? AS BIGINT)", [value]) if isinstance(value, int) else ("CAST(? AS DECIMAL(38,10))", [value])


def stage_query(conn: Connection, *, cfg, layout_for, ctx: LoadContext, sql: str) -> tuple[int, Layout]:
    """Same-system source: clear the staging rows and copy the query's rows with one INSERT ... SELECT on Teradata.

    `layout_for(names)` = the layout for the query's column names. No row passes through this process; returns
    (rows copied, layout).
    """
    body = sql_body(f"{cfg.label}: Src_Sql_Txt", sql)
    order = [n.lower() for n in PLACEHOLDER.findall(body)]
    params = ctx.sql_params()
    query, values = PLACEHOLDER.sub("?", body), [params[n] for n in order]
    names = conn.execute_qmark(f"SELECT * FROM ({query}) AS q WHERE 1 = 0", values).columns
    layout = layout_for(names)
    resolved = sql_values(conn, layout, ctx)
    position = {stage_col: names[i] for i, stage_col in enumerate(layout.file_cols)}
    table = _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    with conn.transaction():
        start = next_sequence(conn, cfg, layout)
        exprs, head = [], []
        for c in layout.stage:
            value_cd = "BATCH_ID" if c.name == BATCH_COLUMN else layout.mapping.get(c.name)
            if c.name in position:
                exprs.append(f'q."{position[c.name]}"')
            elif value_cd == "ROW_SEQUENCE":
                number = f'{start - 1} + ROW_NUMBER() OVER (ORDER BY q."{names[0]}")'
                exprs.append(f"CAST({number} AS VARCHAR(20))" if c.text else number)
            elif value_cd is None:
                exprs.append("NULL")
            else:
                expr, v = _cast(c, layout.fixed_value(c, ctx, resolved))
                exprs.append(expr)
                head.extend(v)
        _clean_stage(conn, table, cfg, layout, ctx)
        insert = (f"INSERT INTO {table} ({', '.join(db.ident(c.name) for c in layout.stage)}) "
                  f"SELECT {', '.join(exprs)} FROM ({query}) AS q")
        try:
            count = conn.execute_qmark(insert, [*head, *values]).rowcount
        except Exception as e:  # noqa: BLE001
            if not db.is_data_error(e):
                raise
            raise FileRejected("SOURCE_DATA_REJECTED", "value does not fit staging column types: "
                                                       f"{sanitize_db_error(db.error_line(e))}")
    return count, layout


def _stage_fastload(conn: Connection, settings: "Settings", cfg: "FileConfig", layout: Layout, ctx: LoadContext,
                    records: Rows, resolved: dict, table: str, names: str, width: int) -> int:
    """LOAD_ENGINE=FASTLOAD: FastLoad into a work table of this load, then one INSERT ... SELECT into staging.

    The work table (<staging table>_W<Load_ID>, in the staging database) is dropped afterwards.
    """
    work = _table(cfg.stg_schema_nm, f"{cfg.stg_table_nm}_W{ctx.load_id}")
    _drop(conn, work)
    start = next_sequence(conn, cfg, layout)
    conn.execute(f"CREATE MULTISET TABLE {work} AS (SELECT {names} FROM {table}) WITH NO DATA NO PRIMARY INDEX")
    try:
        try:
            sent = conn.fastload(f"INSERT INTO {work} ({names}) VALUES ({', '.join(['%s'] * width)})",
                                 layout.stage_rows(records, ctx, start, resolved),
                                 settings.insert_batch_rows, settings.insert_batch_bytes)
        except FileRejected:
            raise
        except Exception as e:  # noqa: BLE001
            if not (isinstance(e, db.FastLoadRejected) or db.is_data_error(e)):
                raise
            raise FileRejected("FILE_PARSE_ERROR", "value does not fit staging column types: "
                                                   f"{sanitize_db_error(db.error_line(e))}")
        with conn.transaction():
            _clean_stage(conn, table, cfg, layout, ctx)
            copied = conn.execute(f"INSERT INTO {table} ({names}) SELECT {names} FROM {work}").rowcount
        if copied != sent:
            raise RowCountMismatch(f"staged {copied} of {sent} rows for load {ctx.load_id}")
        return sent
    finally:
        _drop(conn, work)


def _drop(conn: Connection, table: str) -> None:
    try:
        conn.execute(f"DROP TABLE {table}")
    except Exception as e:  # noqa: BLE001
        if db.error_code(e) != 3807:
            log.warning("could not drop work table %s: %s", table, db.error_line(e))


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
    deleted_cnt: int
    appended_cnt: int


def swap(conn, cfg: "FileConfig", layout: Layout, ctx: LoadContext, expected_rows: int) -> PromotionResult:
    """Delete the batch's core rows and insert the staged ones; runs inside the caller's transaction."""
    core, stg = _table(cfg.core_schema_nm, cfg.core_table_nm), _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    deleted = conn.execute(f"DELETE FROM {core} WHERE {db.ident(BATCH_COLUMN)} = %s", (ctx.btch_id,)).rowcount
    names, exprs, values = [], [], []
    for c, kind, _ in layout.core_plan():
        names.append(db.ident(c.name))
        if kind == "copy":
            exprs.append(db.ident(c.name))
        elif kind == "flag":
            exprs.append("'1'" if c.text else "1")
        else:
            exprs.append("CAST(%s AS VARCHAR(50))" if c.text else "CAST(%s AS DATE)" if c.temporal == "DATE"
                         else "CAST(%s AS TIMESTAMP(0))" if c.temporal == "TIMESTAMP"
                         else "CAST(%s AS TIMESTAMP(0) WITH TIME ZONE)")
            values.append(ctx.value("LOAD_TIMESTAMP", c.text, c.temporal or "TIMESTAMP_TZ"))
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
    return PromotionResult(deleted, appended)
