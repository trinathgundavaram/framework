"""Staging load and core promotion (design §9.4, §10)."""
from __future__ import annotations

import codecs
import csv
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

import psycopg
from psycopg import sql

from . import db
from .common import ConfigError, FileRejected, RowCountMismatch
from .filecheck_options import CheckOptions, trailer_problems
from .layout import BATCH_COLUMN, PLACEHOLDER, Column, Layout, LoadContext, build_layout, column_mapping, sql_body

if TYPE_CHECKING:
    from .config import FileConfig
    from .settings import Settings

def columns(conn: psycopg.Connection, schema: str, table: str) -> list[Column]:
    """Columns of a table in definition order; auto = identity / generated / serial, text = character column."""
    rows = conn.execute(
        """SELECT column_name, is_identity, is_generated, column_default, data_type, character_maximum_length,
                  is_nullable FROM information_schema.columns
            WHERE table_schema = lower(%s) AND table_name = lower(%s) ORDER BY ordinal_position""",
        (schema, table)).fetchall()
    return [Column(r["column_name"], r["is_identity"] == "YES" or r["is_generated"] == "ALWAYS"
                   or "nextval(" in (r["column_default"] or ""),
                   r["data_type"] in ("character varying", "character", "text"), r["character_maximum_length"],
                   r["data_type"] in ("smallint", "integer", "bigint", "numeric", "real", "double precision"),
                   r["is_nullable"] == "YES") for r in rows]


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

    `opts` = the file's check options: a HEADER_NAMES row is the list of columns the file has, a COLUMN_COUNT row
    the number of its fields; staging columns the file does not have are loaded as NULL.
    """
    count = None
    if opts is not None and opts.column_count:
        count = opts.column_count - (1 if opts.record_types else 0)
    return build_layout(f"{cfg.project_cd}/{cfg.table_nm}/{cfg.src_id}",
                        columns(conn, cfg.stg_schema_nm, cfg.stg_table_nm),
                        columns(conn, cfg.core_schema_nm, cfg.core_table_nm), column_mapping(cfg, target_columns),
                        f"{cfg.stg_schema_nm}.{cfg.stg_table_nm}", f"{cfg.core_schema_nm}.{cfg.core_table_nm}",
                        header_columns(cfg, opts), count)


def _ident(schema: str, table: str) -> sql.Identifier:
    return sql.Identifier(schema.lower(), table.lower())


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
            names = {n.lower() for n in PLACEHOLDER.findall(body)}
            query = PLACEHOLDER.sub(lambda m: f"%({m.group(1).lower()})s", body.replace("%", "%%"))
            try:
                with conn.transaction():
                    conn.execute("SET TRANSACTION READ ONLY")
                    rows = conn.execute(query, {n: params[n] for n in names}).fetchall()
            except psycopg.Error as e:
                raise ConfigError(f"{what} failed: {db.error_line(e)}") from None
            if len(rows) != 1 or len(rows[0]) != 1:
                raise ConfigError(f"{what} must return one row with one column (it returned {len(rows)} row(s))")
            done[text] = next(iter(rows[0].values()))
        out[c.name] = done[text]
    return out


def next_sequence(conn, cfg: "FileConfig", layout: Layout) -> int:
    """The next value of the ROW_SEQUENCE column: one above the highest in the staging and core tables."""
    highest = 0
    for kind, table in (("stage", _ident(cfg.stg_schema_nm, cfg.stg_table_nm)),
                        ("core", _ident(cfg.core_schema_nm, cfg.core_table_nm))):
        col = layout.column("ROW_SEQUENCE", kind)
        if col:
            row = conn.execute(sql.SQL("SELECT MAX(CAST({} AS BIGINT)) AS m FROM {}").format(
                sql.Identifier(col.name), table)).fetchone()
            highest = max(highest, int(row["m"] or 0))
    return highest + 1


def _clean_stage(conn, table, cfg: "FileConfig", layout: Layout, ctx: LoadContext) -> None:
    """Remove the staging rows of the file's run type; of its batch when the table has no run type column."""
    col = layout.column("RUN_TYPE")
    if col:
        conn.execute(sql.SQL("DELETE FROM {} WHERE {} = %s").format(table, sql.Identifier(col.name)),
                     (ctx.value("RUN_TYPE", col.text),))
    else:
        conn.execute(sql.SQL("DELETE FROM {} WHERE {} = %s").format(table, sql.Identifier(BATCH_COLUMN)), (ctx.btch_id,))


def stage(conn: psycopg.Connection, settings: "Settings", *, file_path: str, cfg: "FileConfig", layout: Layout,
          ctx: LoadContext, opts: Optional[CheckOptions] = None, spark=None) -> int:
    """Clear the staging rows (of the batch, or of its run type) and stream the file into COPY; returns the row count."""
    table = _ident(cfg.stg_schema_nm, cfg.stg_table_nm)
    if settings.load_engine == "SPARK":
        return _stage_spark(conn, settings, spark, file_path, cfg, layout, ctx, opts)
    records = read_file(file_path, cfg, len(layout.file_cols), settings, opts)
    cols = [c.name for c in layout.stage]
    count = 0
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(table, sql.SQL(", ").join(map(sql.Identifier, cols)))
    resolved = sql_values(conn, layout, ctx)
    try:
        with conn.transaction():
            start = next_sequence(conn, cfg, layout)
            _clean_stage(conn, table, cfg, layout, ctx)
            with conn.cursor() as cur, cur.copy(copy_sql) as cp:
                for row in layout.stage_rows(records, ctx, start, resolved):
                    cp.write_row(row)
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


def _stage_spark(conn, settings, spark, file_path, cfg, layout, ctx, opts):
    """Spark engine: streaming scan, then JDBC append."""
    from pyspark.sql import SparkSession, functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    if cfg.src_file_ty not in (".txt", ".csv") or cfg.has_trailer:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "Spark engine reads delimited files without trailer only")
    if not settings.spark_jdbc_url:
        raise ConfigError("LOAD_ENGINE=SPARK requires SPARK_JDBC_URL")
    if (opts and opts.record_types) or layout.column("ROW_SEQUENCE"):
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "Spark engine does not load files with record types or a "
                                                      "ROW_SEQUENCE column")
    stg_columns = layout.file_cols
    resolved = sql_values(conn, layout, ctx)
    rows = scan_delimited(file_path, cfg, len(stg_columns), settings)
    spark = spark or SparkSession.builder.appName("cms-compliance-framework").getOrCreate()
    df = (spark.read.option("header", str(cfg.has_header).lower()).option("sep", delimiter_for(cfg))
          .option("quote", settings.quote_char).option("encoding", settings.file_encoding).option("mode", "FAILFAST")
          .schema(StructType([StructField(c, StringType(), True) for c in stg_columns])).csv(file_path))
    if settings.empty_as_null:
        df = df.select([F.when(F.col(c) == "", None).otherwise(F.col(c)).alias(c) for c in stg_columns])
    for c in layout.stage:
        if c.name not in stg_columns:
            derived = c.name == BATCH_COLUMN or c.name in layout.mapping
            df = df.withColumn(c.name, F.lit(layout.fixed_value(c, ctx, resolved) if derived else None))
    with conn.transaction():
        _clean_stage(conn, _ident(cfg.stg_schema_nm, cfg.stg_table_nm), cfg, layout, ctx)
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
    deleted_cnt: int
    appended_cnt: int


def swap(conn, cfg: "FileConfig", layout: Layout, ctx: LoadContext, expected_rows: int) -> PromotionResult:
    """Delete the batch's core rows and insert the staged ones; runs inside the caller's transaction."""
    core, stg = _ident(cfg.core_schema_nm, cfg.core_table_nm), _ident(cfg.stg_schema_nm, cfg.stg_table_nm)
    batch_col = sql.Identifier(BATCH_COLUMN)
    deleted = conn.execute(sql.SQL("DELETE FROM {} WHERE {} = %s").format(core, batch_col), (ctx.btch_id,)).rowcount
    names, exprs, values = [], [], []
    for c, kind, _ in layout.core_plan():
        names.append(sql.Identifier(c.name))
        if kind == "copy":
            exprs.append(sql.Identifier(c.name))
        elif kind == "flag":
            exprs.append(sql.Literal("1" if c.text else 1))
        else:
            exprs.append(sql.SQL("%s"))
            values.append(ctx.value("LOAD_TIMESTAMP", c.text))
    where = sql.SQL("{} = %s").format(batch_col)
    values.append(ctx.btch_id)
    load_col = layout.column("LOAD_ID", "stage")
    if load_col:
        where += sql.SQL(" AND {} = %s").format(sql.Identifier(load_col.name))
        values.append(ctx.value("LOAD_ID", load_col.text))
    appended = conn.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} WHERE {}").format(
        core, sql.SQL(", ").join(names), sql.SQL(", ").join(exprs), stg, where), values).rowcount
    if appended != expected_rows:
        raise RowCountMismatch(f"appended {appended} rows for load {ctx.load_id}, staged {expected_rows}")
    return PromotionResult(deleted, appended)
