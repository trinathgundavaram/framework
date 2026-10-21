"""Staging / core table layout, the load into staging and the core swap, the control report and error log."""
from __future__ import annotations

import codecs
import csv
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Iterator, Mapping, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from compliance_framework import db
from compliance_framework.adapters import basename, dirname
from compliance_framework.common import ConfigError, FileRejected, RowCountMismatch, code
from compliance_framework.config import CheckOptions, MatchError, trailer_problems
from compliance_framework.db import Connection


# layout: Which columns of a file's staging and core tables the framework fills, and with what (ComplianceTargetColumnConfig).

BATCH_COLUMN = "btch_id"
VALUE_CODES = ("BATCH_ID", "RUN_TYPE", "VERSION", "SOURCE_ID", "FILE_NAME", "LOAD_TIMESTAMP", "ROW_SEQUENCE", "LOAD_ID",
               "CURRENT_FLAG", "END_TIMESTAMP", "PROJECT", "TABLE", "RPT_START", "RPT_END", "REQ_ID", "LOAD_DATE",
               "CONSTANT", "SQL")
TEXT_CODES = ("CONSTANT", "SQL")
SQL_PARAMS = ("btch_id", "run_ty", "version", "src_id", "file_name", "project_cd", "table_nm", "load_id", "req_id",
              "rpt_start", "rpt_end", "load_date")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
DEFAULT_MAPPING = {"src_id": "SOURCE_ID", "load_id": "LOAD_ID", "src_file_nm": "FILE_NAME", "stg_load_dtts": "LOAD_TIMESTAMP",
                   "current_ind": "CURRENT_FLAG", "load_dtts": "LOAD_TIMESTAMP", "end_dtts": "END_TIMESTAMP"}
TIMESTAMP_TEXT = "%Y-%m-%d %H:%M:%S"
TEMPORAL_CODES = ("LOAD_DATE", "LOAD_TIMESTAMP", "RPT_START", "RPT_END")


@dataclass(frozen=True)
class Column:
    name: str
    auto: bool = False
    text: bool = False
    length: Optional[int] = None
    numeric: bool = False
    nullable: bool = True
    temporal: Optional[str] = None
    cast: Optional[str] = None
    default: bool = False


@dataclass(frozen=True)
class LoadContext:
    """The values of one load that derived columns are filled from."""
    btch_id: str
    load_id: int
    run_ty: str
    version: Optional[str]
    src_id: str
    file_name: str
    loaded_at: datetime
    project_cd: str
    table_nm: str
    rpt_start: Optional[date]
    rpt_end: Optional[date]
    req_id: int
    tz: str = "UTC"

    def value(self, value_cd: str, text: bool, temporal: Optional[str] = None):
        """The value of a derived column; `text` = a character column, `temporal` = its date / time type if known.

        A load time for a TIMESTAMP column is sent as text without fractional seconds, so the database converts
        it to the column's own precision (a TIMESTAMP(6) value does not fit a TIMESTAMP(0) column on Teradata).
        """
        if value_cd == "LOAD_TIMESTAMP":
            local = self.loaded_at.astimezone(ZoneInfo(self.tz))
            if text or temporal == "TIMESTAMP":
                return local.strftime(TIMESTAMP_TEXT)
            if temporal == "DATE":
                return local.date()
            return local.isoformat(sep=" ", timespec="seconds") if temporal == "TIMESTAMP_TZ" else self.loaded_at
        v = {"BATCH_ID": self.btch_id, "RUN_TYPE": self.run_ty, "VERSION": self.version, "SOURCE_ID": self.src_id,
             "FILE_NAME": self.file_name, "LOAD_ID": self.load_id, "CURRENT_FLAG": 1, "END_TIMESTAMP": None,
             "LOAD_DATE": self.loaded_at.astimezone(ZoneInfo(self.tz)).date(),
             "PROJECT": self.project_cd, "TABLE": self.table_nm, "RPT_START": self.rpt_start, "RPT_END": self.rpt_end,
             "REQ_ID": self.req_id}[value_cd]
        if temporal == "TIMESTAMP" and not text and isinstance(v, date):
            return f"{v.isoformat()} 00:00:00"
        return str(v) if text and v is not None and not isinstance(v, str) else v

    def sql_params(self) -> dict:
        """The values a Value_Txt statement of a SQL column can use as {name}."""
        return {"btch_id": self.btch_id, "run_ty": self.run_ty, "version": self.version, "src_id": self.src_id,
                "file_name": self.file_name, "project_cd": self.project_cd, "table_nm": self.table_nm,
                "load_id": self.load_id, "req_id": self.req_id, "rpt_start": self.rpt_start, "rpt_end": self.rpt_end,
                "load_date": self.value("LOAD_DATE", False)}


class ColumnMapping(dict):
    """{column: value code}; `texts` holds the Value_Txt of the CONSTANT and SQL columns."""

    def __init__(self, *args, texts: Optional[dict] = None):
        super().__init__(*args)
        self.texts = texts or {}


def sql_body(label: str, text: str) -> str:
    """The statement of a SQL column, checked: a single SELECT that only uses the known {names}."""
    body = text.strip().rstrip(";").strip()
    if ";" in body or not re.match(r"^(SELECT|WITH)\b", body, re.IGNORECASE):
        raise ConfigError(f"{label}: Value_Txt of a SQL column must be a single SELECT statement")
    unknown = sorted({n.lower() for n in PLACEHOLDER.findall(body)} - set(SQL_PARAMS))
    if unknown:
        raise ConfigError(f"{label}: Value_Txt uses {{{unknown[0]}}}; available: "
                          f"{', '.join('{' + n + '}' for n in SQL_PARAMS)}")
    return body


def column_mapping(cfg, rows: Iterable) -> ColumnMapping:
    """{column: value code} for a file config: its table's rows over the project's '*' rows.

    Without any row for the table the framework's own column names apply (DEFAULT_MAPPING).
    """
    chosen: dict = {}
    for r in rows:
        if not r.active or r.project_cd != cfg.project_cd or r.table_nm not in ("*", cfg.table_nm):
            continue
        if r.column_nm not in chosen or r.table_nm != "*":
            chosen[r.column_nm] = r
    if not chosen:
        return ColumnMapping(DEFAULT_MAPPING)
    out = ColumnMapping()
    for name, r in chosen.items():
        if r.value_cd not in VALUE_CODES:
            raise ConfigError(f"{r.label}: unknown Value_Cd {r.value_cd!r}; accepted: {', '.join(VALUE_CODES)}")
        if name == BATCH_COLUMN:
            raise ConfigError(f"{r.label}: {BATCH_COLUMN} is always the batch ID and needs no row")
        if r.value_cd in TEXT_CODES:
            if r.value_txt is None:
                raise ConfigError(f"{r.label}: {r.value_cd} needs a value in Value_Txt")
            if r.value_cd == "SQL":
                sql_body(r.label, r.value_txt)
            out.texts[name] = r.value_txt
        elif r.value_txt is not None:
            raise ConfigError(f"{r.label}: Value_Txt is only used with Value_Cd CONSTANT or SQL")
        out[name] = r.value_cd
    return out


def batch_column(names: Iterable[str], mapping: Mapping) -> Optional[str]:
    """A table's batch column: the one mapped to BATCH_ID (e.g. Batch_ID), else Btch_ID; None when it has neither."""
    names = list(names)
    return next((n for n in names if mapping.get(n) == "BATCH_ID"), None) or (BATCH_COLUMN if BATCH_COLUMN in names else None)


@dataclass(frozen=True)
class Layout:
    """The staging and core columns of one file config and how each is filled."""
    label: str
    stage: tuple
    core: tuple
    mapping: dict
    texts: dict = field(default_factory=dict)
    names: tuple = ()
    count: Optional[int] = None
    targets: tuple = ()

    @property
    def free_cols(self) -> list[str]:
        """The staging columns the framework does not fill itself, in table order."""
        return [c.name for c in self.stage if c.name != BATCH_COLUMN and c.name not in self.mapping]

    @property
    def file_cols(self) -> list[str]:
        """The staging column of each field of the file, in file order.

        With a target column list (`targets`, from TARGET_COLUMNS) field n goes to the n-th listed column. Otherwise,
        with a column list (`names`, from HEADER_NAMES) the fields go to the columns of the same name when every
        name is a column, otherwise to the first free columns in table order. With only a field count (`count`,
        from COLUMN_COUNT) they go to the first `count` free columns. Without either every free column is a field.
        The free columns left over are loaded as NULL.
        """
        free = self.free_cols
        if self.targets:
            by_name = {c.casefold(): c for c in free}
            return [by_name[n.casefold()] for n in self.targets]
        if self.names:
            by_name = {c.casefold(): c for c in free}
            wanted = [n.casefold() for n in self.names]
            if len(set(wanted)) == len(wanted) and all(n in by_name for n in wanted):
                return [by_name[n] for n in wanted]
            return free[:len(self.names)]
        return free[:self.count] if self.count else free

    @property
    def stage_batch(self) -> str:
        return batch_column((c.name for c in self.stage), self.mapping)

    @property
    def core_batch(self) -> str:
        return batch_column((c.name for c in self.core), self.mapping)

    def column(self, value_cd: str, table: str = "stage") -> Optional[Column]:
        return next((c for c in getattr(self, table) if self.mapping.get(c.name) == value_cd), None)

    def core_plan(self) -> list[tuple[Column, str, Optional[str]]]:
        """[(core column, 'copy' | 'cast' | 'flag' | 'batch' | 'timestamp' | 'value', staging column)]: how each
        inserted core column gets its value.

        A core column takes the staging column of the same name; a derived one (ComplianceTargetColumnConfig) the
        staging column with the same value code. The data columns left on both sides (names that differ) are paired
        in table order: the n-th of them in staging fills the n-th of them in core; core columns left after that stay
        empty. 'value' = a date the framework fills, staged as text, sent in the core column's own type; 'cast' = a
        text staging value converted to the core column's type (and FORMAT).
        """
        by_name = {c.name.casefold(): c for c in self.stage}
        by_code = {self.mapping[c.name]: c for c in self.stage if c.name in self.mapping}
        core_names = {c.name.casefold() for c in self.core}
        left_stage = [c for c in self.stage if c.name.casefold() not in core_names and c.name not in self.mapping
                      and c.name != BATCH_COLUMN]
        left_core = [c for c in self.core if c.name.casefold() not in by_name and c.name not in self.mapping
                     and c.name != BATCH_COLUMN]
        source = {c.name: by_name.get(c.name.casefold()) or by_code.get(self.mapping.get(c.name)) for c in self.core}
        source.update(zip((c.name for c in left_core), left_stage))
        plan = []
        for c in self.core:
            value_cd = self.mapping.get(c.name)
            s = source.get(c.name)
            if value_cd == "END_TIMESTAMP":
                continue
            if value_cd == "CURRENT_FLAG":
                plan.append((c, "flag", None))
            elif c.name == self.core_batch:
                plan.append((c, "batch", self.stage_batch))
            elif s and self.mapping.get(s.name) in TEMPORAL_CODES and c.temporal and s.text:
                plan.append((c, "value", s.name))
            elif s and s.text and not c.text and c.cast:
                plan.append((c, "cast", s.name))
            elif s:
                plan.append((c, "copy", s.name))
            elif value_cd == "LOAD_TIMESTAMP":
                plan.append((c, "timestamp", None))
            elif value_cd:
                raise ConfigError(f"{self.label}: core column {c.name} ({value_cd}) must also be a column of the "
                                  "staging table")
        return plan

    def renamed(self) -> list[str]:
        """'staging -> core' for the pairs whose names differ."""
        return [f"{s} -> {c.name}" for c, _, s in self.core_plan() if s and s.casefold() != c.name.casefold()]

    def fixed_value(self, c: Column, ctx: LoadContext, resolved: Optional[dict] = None):
        """The value of a derived staging column that is the same on every row of a load."""
        value_cd = "BATCH_ID" if c.name == BATCH_COLUMN else self.mapping[c.name]
        if value_cd == "CONSTANT":
            return self.texts[c.name]
        if value_cd == "SQL":
            v = (resolved or {})[c.name]
            return str(v) if c.text and v is not None and not isinstance(v, str) else v
        return ctx.value(value_cd, c.text, c.temporal)

    def stage_rows(self, records: Iterable[list], ctx: LoadContext, sequence_start: int = 1,
                   resolved: Optional[dict] = None) -> Iterator[list]:
        """Each file record as a full staging row, in staging column order; `resolved` = the SQL columns' values."""
        slots = []
        position = {name: i for i, name in enumerate(self.file_cols)}
        for c in self.stage:
            value_cd = "BATCH_ID" if c.name == BATCH_COLUMN else self.mapping.get(c.name)
            if value_cd is None and c.name in position:
                slots.append(("file", position[c.name], False))
            elif value_cd is None:
                slots.append(("value", None, False))
            elif value_cd == "ROW_SEQUENCE":
                slots.append(("seq", None, c.text))
            else:
                slots.append(("value", self.fixed_value(c, ctx, resolved), False))
        for n, rec in enumerate(records):
            yield [rec[value] if kind == "file" else value if kind == "value"
                   else (str(sequence_start + n) if text else sequence_start + n) for kind, value, text in slots]


def build_layout(label: str, stage_cols: list[Column], core_cols: list[Column], mapping: dict, stage_name: str,
                 core_name: str, names: tuple = (), count: Optional[int] = None, targets: tuple = ()) -> Layout:
    """Check the two tables against the mapping; LookupError = a table or a required column is missing."""
    if not stage_cols:
        raise LookupError(f"staging table {stage_name} not found")
    if not core_cols:
        raise LookupError(f"core table {core_name} not found")
    stage = tuple(c for c in stage_cols if not c.auto)
    core = tuple(c for c in core_cols if not c.auto)
    for name, cols in ((stage_name, stage), (core_name, core)):
        if not batch_column((c.name for c in cols), mapping):
            raise LookupError(f"table {name} lacks the batch column {BATCH_COLUMN} (or a column mapped to BATCH_ID)")
    layout = Layout(label, stage, core, {k: v for k, v in mapping.items()
                                         if k in {c.name for c in stage} | {c.name for c in core}},
                    dict(getattr(mapping, "texts", {})), tuple(names), count, tuple(targets))
    for side, name in (("stage", stage_name), ("core", core_name)):
        if not layout.column("SOURCE_ID", side):
            raise LookupError(f"{label}: table {name} needs a column mapped to SOURCE_ID in ComplianceTargetColumnConfig "
                              "(a batch holds every source of the table; its rows are told apart by that column)")
    if layout.column("ROW_SEQUENCE", "core") and not layout.column("ROW_SEQUENCE", "stage"):
        raise LookupError(f"{label}: the ROW_SEQUENCE column must also be in the staging table {stage_name}")
    if not layout.free_cols:
        raise LookupError(f"staging table {stage_name} has no column left for the file's data")
    fillable = {c.casefold() for c in layout.free_cols}
    unknown = [n for n in targets if n.casefold() not in fillable]
    if unknown:
        raise LookupError(f"{label}: TARGET_COLUMNS names {unknown[0]}, which is not a column of {stage_name} the file "
                          "can fill (a column of ComplianceTargetColumnConfig or Btch_ID cannot be listed)")
    wanted, source = ((len(targets), "TARGET_COLUMNS lists") if targets else (len(names), "HEADER_NAMES lists") if names
                      else (count, "COLUMN_COUNT gives"))
    if wanted and wanted > len(layout.free_cols):
        raise LookupError(f"{label}: {source} {wanted} data columns, staging table {stage_name} has "
                          f"{len(layout.free_cols)} for the file's data")
    renamed = layout.renamed()
    if renamed:
        log.info("%s | staging -> core by column position: %s", label, ", ".join(renamed))
    return layout


# load: Staging load and core promotion on Teradata (design §9.4, §10).

if TYPE_CHECKING:
    from compliance_framework.config import FileConfig
    from compliance_framework.db import Settings

log = logging.getLogger(__name__)

def columns(conn: Connection, schema: str, table: str) -> list[Column]:
    """Columns of a table in definition order (lower-case names); auto = identity, text = character column,
    cast = the column's type (with its FORMAT) that a text value is converted to."""
    rows = conn.execute(
        """SELECT * FROM DBC.ColumnsV WHERE UPPER(DatabaseName) = UPPER(%s) AND UPPER(TableName) = UPPER(%s)
            ORDER BY ColumnId""", (schema, table)).fetchall()
    out = []
    for r in rows:
        kind = (r["columntype"] or "").strip().upper()
        length = None
        if kind in ("CV", "CF") and r["columnlength"]:
            length = int(r["columnlength"]) // (2 if int(r["chartype"] or 1) == 2 else 1)
        out.append(Column(r["columnname"].strip().lower(), bool((r["idcoltype"] or "").strip()),
                          kind in ("CV", "CF", "CO"), length, kind in ("I", "I1", "I2", "I8", "D", "F", "N"),
                          (r["nullable"] or "Y").strip().upper() != "N",
                          {"DA": "DATE", "TS": "TIMESTAMP", "SZ": "TIMESTAMP_TZ"}.get(kind), _cast_type(kind, r),
                          bool((r.get("defaultvalue") or "").strip())))
    return out


def _cast_type(kind: str, r: dict) -> Optional[str]:
    """The SQL type of a DBC.ColumnsV row, as CAST takes it; None for character and other types (copied as they are)."""
    total, frac = r.get("decimaltotaldigits"), r.get("decimalfractionaldigits")
    fmt = (r.get("columnformat") or "").strip()
    fmt = f" FORMAT '{fmt}'" if fmt else ""
    p = f"({int(frac)})" if frac is not None and int(frac) >= 0 else ""
    types = {"I": "INTEGER", "I1": "BYTEINT", "I2": "SMALLINT", "I8": "BIGINT", "F": "FLOAT",
             "D": f"DECIMAL({int(total)},{int(frac or 0)})" if total else None,
             "N": f"NUMBER({int(total)},{int(frac or 0)})" if total and 0 < int(total) <= 38 else "NUMBER",
             "DA": f"DATE{fmt}", "TS": f"TIMESTAMP{p}{fmt}", "SZ": f"TIMESTAMP{p} WITH TIME ZONE{fmt}",
             "AT": f"TIME{p}{fmt}", "TZ": f"TIME{p} WITH TIME ZONE{fmt}"}
    return types.get(kind)


def header_columns(cfg: "FileConfig", opts) -> tuple:
    """The file's column names from its HEADER_NAMES check row; () when it has none."""
    if opts is None or not opts.header:
        return ()
    try:
        delim = delimiter_for(cfg)
    except ConfigError:
        delim = ","
    names = [v.strip() for v in opts.header.split(delim if delim in opts.header else ",")]
    if opts.record_types and names[0].upper() == opts.record_types[0]:
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
        problems = trailer_problems(held[1], held[0], os.path.basename(path), opts, data_rows, lines)
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
            db.log_sql(what, body, params)
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
        src = layout.column("SOURCE_ID")
        conn.execute(f"DELETE FROM {table} WHERE {db.ident(layout.stage_batch)} = %s AND {db.ident(src.name)} = %s",
                     (ctx.btch_id, ctx.value("SOURCE_ID", src.text, src.temporal)))


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
    if isinstance(value, datetime):
        return "CAST(? AS TIMESTAMP(0) WITH TIME ZONE)", [value]
    if isinstance(value, date):
        return "CAST(? AS DATE)", [value]
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
    db.log_sql(f"SOURCE_LOAD | {ctx.btch_id} source {ctx.src_id} | Src_Sql_Txt (same Teradata system)", body, params)
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


_TEMPORAL_CAST = {"DATE": "CAST(%s AS DATE)", "TIMESTAMP": "CAST(%s AS TIMESTAMP(0))",
                  "TIMESTAMP_TZ": "CAST(%s AS TIMESTAMP(0) WITH TIME ZONE)"}


def _temporal_value(v, temporal: str, tz: str):
    """A date or load time as the core column's type takes it (a date in a TIMESTAMP column = its midnight)."""
    if v is None or isinstance(v, str) or temporal == "DATE":
        return v.date() if isinstance(v, datetime) else v
    at = v if isinstance(v, datetime) else datetime(v.year, v.month, v.day, tzinfo=ZoneInfo(tz))
    return at.strftime(TIMESTAMP_TEXT) if temporal == "TIMESTAMP" else at.isoformat(sep=" ", timespec="seconds")


def _converted(c: Column, staged: str) -> str:
    """The staging column `staged` converted to core column `c`'s type."""
    return f"CAST(NULLIF(TRIM({db.ident(staged)}), '') AS {c.cast})"


def _staged(layout: Layout, ctx: LoadContext) -> tuple[str, list]:
    """The WHERE clause (and its values) of the load's rows in the staging table."""
    stage_src = layout.column("SOURCE_ID")
    where = f"{db.ident(layout.stage_batch)} = %s AND {db.ident(stage_src.name)} = %s"
    values = [ctx.btch_id, ctx.value("SOURCE_ID", stage_src.text, stage_src.temporal)]
    load_col = layout.column("LOAD_ID", "stage")
    if load_col:
        where += f" AND {db.ident(load_col.name)} = %s"
        values.append(ctx.value("LOAD_ID", load_col.text))
    return where, values


def check_core_types(conn, cfg, layout: Layout, ctx: LoadContext) -> None:
    """Every NOT NULL core column gets a value, every staged text value that goes to a core column of another type
    converts to that type (and FORMAT). Otherwise the load's staged rows are removed and the load is rejected."""
    stg = _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    where, keys = _staged(layout, ctx)
    plan = layout.core_plan()
    filled = {c.name for c, _, _ in plan}
    problems = [f"core column {c.name} is NOT NULL but gets no value (no staging column at its position)"
                for c in layout.core if not c.nullable and not c.default and c.name not in filled]
    for c, kind, staged in plan:
        if kind != "cast":
            continue
        try:
            conn.execute(f"SELECT COUNT({_converted(c, staged)}) AS n FROM {stg} WHERE {where}", keys).fetchone()
        except Exception as e:  # noqa: BLE001
            if conn.in_transaction:
                raise
            problems.append(f"{staged} -> {c.name}: a value cannot be converted to {c.cast} "
                            f"({sanitize_db_error(db.error_line(e))})")
            continue
        if not c.nullable and conn.execute(f"SELECT COUNT(*) AS n FROM {stg} WHERE {where} AND "
                                           f"NULLIF(TRIM({db.ident(staged)}), '') IS NULL", keys).fetchone()["n"]:
            problems.append(f"{staged} -> {c.name}: empty value for a NOT NULL column")
    if problems:
        with conn.transaction():
            conn.execute(f"DELETE FROM {stg} WHERE {where}", keys)
        raise FileRejected("CORE_TYPE_MISMATCH", f"values do not fit the core table {cfg.core_schema_nm}."
                           f"{cfg.core_table_nm}: {'; '.join(problems)}")


@dataclass
class PromotionResult:
    deleted_cnt: int
    appended_cnt: int


def swap(conn, cfg: "FileConfig", layout: Layout, ctx: LoadContext, expected_rows: int) -> PromotionResult:
    """Delete the batch's core rows and insert the staged ones; runs inside the caller's transaction."""
    core, stg = _table(cfg.core_schema_nm, cfg.core_table_nm), _table(cfg.stg_schema_nm, cfg.stg_table_nm)
    core_src, stage_src = layout.column("SOURCE_ID", "core"), layout.column("SOURCE_ID")
    deleted = conn.execute(f"DELETE FROM {core} WHERE {db.ident(layout.core_batch)} = %s AND {db.ident(core_src.name)} = %s",
                           (ctx.btch_id, ctx.value("SOURCE_ID", core_src.text, core_src.temporal))).rowcount
    names, exprs, values = [], [], []
    for c, kind, staged in layout.core_plan():
        names.append(db.ident(c.name))
        if kind == "copy":
            exprs.append(db.ident(staged))
        elif kind == "cast":
            exprs.append(_converted(c, staged))
        elif kind == "batch":
            exprs.append(db.ident(staged))
        elif kind == "flag":
            exprs.append("'1'" if c.text else "1")
        elif kind == "value":
            exprs.append(_TEMPORAL_CAST[c.temporal])
            values.append(_temporal_value(ctx.value(layout.mapping[staged], False, c.temporal), c.temporal, ctx.tz))
        else:
            exprs.append("CAST(%s AS VARCHAR(50))" if c.text else _TEMPORAL_CAST[c.temporal or "TIMESTAMP_TZ"])
            values.append(ctx.value("LOAD_TIMESTAMP", c.text, c.temporal or "TIMESTAMP_TZ"))
    where, keys = _staged(layout, ctx)
    values.extend(keys)
    appended = conn.execute(f"INSERT INTO {core} ({', '.join(names)}) SELECT {', '.join(exprs)} FROM {stg} WHERE {where}",
                            values).rowcount
    if appended != expected_rows:
        raise RowCountMismatch(f"appended {appended} rows for load {ctx.load_id}, staged {expected_rows}")
    return PromotionResult(deleted, appended)


# control_report: The control report of a FILE_LOAD run: one line per file, in <inbound folder>/<CONTROL_REPORT_FOLDER>/.

HEADER = ("Source_System", "File_Name", "Status", "Total_Rows_In_File", "Total_Rows_Loaded", "File_Submitted_Date",
          "File_Processed_Date", "Error_Message")
ACCEPTED = ("FILE_LOADED", "RELOADED")
QUERY_PREFIX = "QUERY:"
REJECTED = ("REJECTED",)
FAILED = ("PROCESS_FAILURE",)
WAITING = ("NO_DATA_YET", "LOAD_INCOMPLETE")
MESSAGES = {
    "FILE_REJECTED_UNPARSEABLE": "The file name does not follow the agreed naming format.",
    "FILE_REJECTED_AMBIGUOUS_TEMPLATE": "The file name fits more than one file setup, so the file could not be identified.",
    "FILE_REJECTED_INVALID_TOKEN": "The file name has a date or version that is not valid.",
    "FILE_REJECTED_RUNTY_NOT_CONFIGURED": "The run type in the file name is not set up.",
    "FILE_REJECTED_NO_BATCH": "No request exists for this run type and reporting period.",
    "FILE_REJECTED_BATCH_CLOSED": "The request for this reporting period is already closed.",
    "FILE_REJECTED_DUPLICATE": "This file was already received and loaded.",
    "FILE_TYPE_NOT_SUPPORTED": "The file type is not supported.",
    "FILE_ZERO_RECORDS_REJECTED": "The file has no data records.",
    "FILE_COLUMN_COUNT_MISMATCH": "Some records do not have the expected number of columns.",
    "FILE_TRAILER_COUNT_MISMATCH": "The record count in the trailer does not match the records in the file.",
    "FILE_PARSE_ERROR": "The file contains data that is not in the expected format.",
    "FILE_CONTENT_CHECK_FAILED": "The file did not pass the file checks.",
    "SOURCE_QUERY_FAILED": "The data could not be read from the source system.",
    "SOURCE_DATA_REJECTED": "The source data is not in the expected format.",
    "CORE_TYPE_MISMATCH": "Some values cannot be converted to the type of their column in the core table.",
    "NO_DATA_YET": "The source has no data for this request yet; it is checked again on the next runs until the due date.",
    "LOAD_INCOMPLETE": "Another run was loading into the same table; this is picked up by the next run.",
    "PROCESS_FAILURE": "The file could not be processed because of a system issue. It will be tried again automatically.",
}
DEFAULT_MESSAGE = "The file could not be loaded."
WITH_LINE = ("FILE_PARSE_ERROR", "FILE_COLUMN_COUNT_MISMATCH", "FILE_CONTENT_CHECK_FAILED")
ERROR_LOG_HEADER = "File_Name|Check_Cd|Line_No|Message|Record"
LOCATION = ("file_share", "file_path")


def _local(value, tz: str) -> str:
    if isinstance(value, datetime):
        return (value.astimezone(ZoneInfo(tz)) if value.tzinfo else value).strftime("%Y-%m-%d %H:%M:%S")
    return "" if value is None else str(value)[:19]


def write_error_log(store, settings, share: str, path: str, rows: list) -> Optional[str]:
    """Write <folder>/<ERROR_LOG_FOLDER>/<file name without extension>_errors.txt: one (check, line, message, record) per row.

    Returns where it was written, or None when error logs are off or the file could not be written.
    """
    if not settings.write_error_file:
        return None
    name = basename(path)
    target = store.free_path(share, f"{dirname(path)}{settings.error_log_folder}/{os.path.splitext(name)[0]}_errors.txt")
    lines = [ERROR_LOG_HEADER, *("|".join("" if v is None else str(v) for v in (name, *row)) for row in rows)]
    try:
        store.put(share, target, ("\n".join(lines) + "\n").encode(settings.file_encoding, "replace"))
    except Exception as e:  # noqa: BLE001
        log.warning("could not write the error log of %s: %s", name, e)
        return None
    return store.uri(share, target)


def business_message(load: dict) -> str:
    """A plain-language reason for a file that was not loaded; never the technical error text."""
    stat = code(load["load_stat"])
    reason = code(load["reject_rsn_cd"]) if stat == "REJECTED" else stat
    text = MESSAGES.get(reason, DEFAULT_MESSAGE)
    line = re.search(r"\bline (\d+)\b", str(load["error_txt"] or "")) if reason in WITH_LINE else None
    return f"{text[:-1]} (first at line {line.group(1)})." if line else text


def report_line(conn, load: dict, matcher, tz: str, sources: dict) -> list:
    name = basename(load[LOCATION[1]])
    src = submitted = None
    if load["cfg_id"] is not None:
        cfg = conn.execute("SELECT Src_ID FROM ComplianceSourceFileConfig WHERE Cfg_ID=%s", (load["cfg_id"],)).fetchone()
        src = code(cfg["src_id"]) if cfg else None
    try:
        submitted = matcher.match(name).file_ts
    except MatchError:
        pass
    stat = code(load["load_stat"])
    accepted = stat in ACCEPTED
    rows = load["stg_rcd_cnt"]
    return [sources.get(src, src), name, "ACCEPTED" if accepted else "REJECTED" if stat in REJECTED else "FAILED" if stat in FAILED
            else "WAITING" if stat in WAITING else stat,
            rows, (rows or 0) if accepted else 0, submitted.date() if submitted else None,
            _local(load["updated_dtts"], tz), None if accepted else business_message(load)]


def file_details(conn, load_ids: list, matcher, tz: str) -> list[dict]:
    """What happened to each file a run handled, for the step report and the control report."""
    sources = {code(r["src_id"]): r["src_nm"] for r in conn.execute(
        "SELECT Src_ID, Src_Nm FROM ComplianceSourceSystem").fetchall()}
    out = []
    for load_id in dict.fromkeys(load_ids):
        load = conn.execute("SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", (load_id,)).fetchone()
        if not load:
            continue
        source, name, status, rows_in, rows_loaded, submitted, processed, reason = report_line(conn, load, matcher, tz,
                                                                                                sources)
        batch = conn.execute("SELECT Req_Stat FROM ComplianceRequestControl WHERE Req_ID=%s",
                             (load["req_id"],)).fetchone() if load["req_id"] is not None else None
        text, _, error_log = str(load["error_txt"] or "").partition("; details in ")
        out.append({"file": name, "status": status, "source": source, "load_id": load_id, "batch": load["btch_id"],
                    "batch_status": batch["req_stat"] if batch else None, "rows_in_file": rows_in,
                    "rows_loaded": rows_loaded, "file_date": submitted, "processed": processed, "reason": reason,
                    "detail": text or None if status != "ACCEPTED" else None, "error_log": error_log or None,
                    "share": load[LOCATION[0]], "folder": dirname(load[LOCATION[1]])})
    return out


def write_reports(store, settings, clock, details: list) -> list[str]:
    """One control report per inbound folder for the files a run handled; returns where they were written."""
    if not settings.write_control_report or not details:
        return []
    by_folder: dict = {}
    for d in details:
        if str(d["share"]).startswith(QUERY_PREFIX):
            continue
        by_folder.setdefault((d["share"], d["folder"]), []).append(
            [d["source"], d["file"], d["status"], d["rows_in_file"], d["rows_loaded"], d["file_date"], d["processed"],
             d["reason"]])
    stamp = clock.now().astimezone(ZoneInfo(settings.business_tz)).strftime("%Y%m%d_%H%M%S")
    written = []
    for (share, folder), lines in by_folder.items():
        target = store.free_path(share, f"{folder}{settings.control_report_folder}/ControlReport_{stamp}.txt")
        text = "\n".join("|".join("" if v is None else str(v) for v in row) for row in [HEADER, *lines]) + "\n"
        try:
            store.put(share, target, text.encode(settings.file_encoding, "replace"))
            written.append(store.uri(share, target))
        except Exception as e:  # noqa: BLE001
            log.warning("could not write the control report %s: %s", target, e)
    return written
