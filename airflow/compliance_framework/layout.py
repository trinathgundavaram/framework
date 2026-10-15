"""Which columns of a file's staging and core tables the framework fills, and with what (ComplianceTargetColumnConfig).

Every other column of the staging table is a column of the file, in table order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

from .common import ConfigError

BATCH_COLUMN = "btch_id"
VALUE_CODES = ("RUN_TYPE", "VERSION", "SOURCE_ID", "FILE_NAME", "LOAD_TIMESTAMP", "ROW_SEQUENCE", "LOAD_ID",
               "CURRENT_FLAG", "END_TIMESTAMP", "PROJECT", "TABLE", "RPT_START", "RPT_END", "REQ_ID", "LOAD_DATE",
               "CONSTANT", "SQL")
TEXT_CODES = ("CONSTANT", "SQL")
SQL_PARAMS = ("btch_id", "run_ty", "version", "src_id", "file_name", "project_cd", "table_nm", "load_id", "req_id",
              "rpt_start", "rpt_end", "load_date")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
DEFAULT_MAPPING = {"src_id": "SOURCE_ID", "load_id": "LOAD_ID", "src_file_nm": "FILE_NAME", "stg_load_dtts": "LOAD_TIMESTAMP",
                   "current_ind": "CURRENT_FLAG", "load_dtts": "LOAD_TIMESTAMP", "end_dtts": "END_TIMESTAMP"}
TIMESTAMP_TEXT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Column:
    name: str
    auto: bool = False
    text: bool = False
    length: Optional[int] = None
    numeric: bool = False
    nullable: bool = True
    temporal: Optional[str] = None


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

    def column(self, value_cd: str, table: str = "stage") -> Optional[Column]:
        return next((c for c in getattr(self, table) if self.mapping.get(c.name) == value_cd), None)

    def core_plan(self) -> list[tuple[Column, str, Optional[str]]]:
        """[(core column, 'copy' | 'flag' | 'timestamp', None)]: how each inserted core column gets its value."""
        staged = {c.name for c in self.stage}
        plan = []
        for c in self.core:
            value_cd = self.mapping.get(c.name)
            if value_cd == "END_TIMESTAMP":
                continue
            if value_cd == "CURRENT_FLAG":
                plan.append((c, "flag", None))
            elif c.name in staged:
                plan.append((c, "copy", None))
            elif value_cd == "LOAD_TIMESTAMP":
                plan.append((c, "timestamp", None))
            elif value_cd:
                raise ConfigError(f"{self.label}: core column {c.name} ({value_cd}) must also be a column of the "
                                  "staging table")
        return plan

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
        if BATCH_COLUMN not in {c.name for c in cols}:
            raise LookupError(f"table {name} lacks the batch column {BATCH_COLUMN}")
    layout = Layout(label, stage, core, {k: v for k, v in mapping.items()
                                         if k in {c.name for c in stage} | {c.name for c in core}},
                    dict(getattr(mapping, "texts", {})), tuple(names), count, tuple(targets))
    if mapping is not DEFAULT_MAPPING and mapping != DEFAULT_MAPPING:
        missing = sorted(k for k in mapping if k not in layout.mapping)
        if missing:
            raise LookupError(f"{label}: column {missing[0]} of ComplianceTargetColumnConfig is in neither {stage_name} "
                              f"nor {core_name}")
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
    layout.core_plan()
    return layout
