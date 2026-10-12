"""Which columns of a file's staging and core tables the framework fills, and with what (ComplianceTargetColumnConfig).

Every other column of the staging table is a column of the file, in table order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

from .common import ConfigError

BATCH_COLUMN = "btch_id"
VALUE_CODES = ("RUN_TYPE", "VERSION", "SOURCE_ID", "FILE_NAME", "LOAD_TIMESTAMP", "ROW_SEQUENCE", "LOAD_ID",
               "CURRENT_FLAG", "END_TIMESTAMP", "PROJECT", "TABLE", "RPT_START", "RPT_END", "REQ_ID")
DEFAULT_MAPPING = {"load_id": "LOAD_ID", "src_file_nm": "FILE_NAME", "stg_load_dtts": "LOAD_TIMESTAMP",
                   "current_ind": "CURRENT_FLAG", "load_dtts": "LOAD_TIMESTAMP", "end_dtts": "END_TIMESTAMP"}
CLEANUP_SCOPES = ("BATCH", "RUN_TYPE")
TIMESTAMP_TEXT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Column:
    name: str
    auto: bool = False
    text: bool = False
    length: Optional[int] = None
    numeric: bool = False
    nullable: bool = True


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

    def value(self, value_cd: str, text: bool):
        """The value of a derived column; `text` = the column is a character column."""
        if value_cd == "LOAD_TIMESTAMP":
            return self.loaded_at.astimezone(ZoneInfo(self.tz)).strftime(TIMESTAMP_TEXT) if text else self.loaded_at
        v = {"BATCH_ID": self.btch_id, "RUN_TYPE": self.run_ty, "VERSION": self.version, "SOURCE_ID": self.src_id,
             "FILE_NAME": self.file_name, "LOAD_ID": self.load_id, "CURRENT_FLAG": 1, "END_TIMESTAMP": None,
             "PROJECT": self.project_cd, "TABLE": self.table_nm, "RPT_START": self.rpt_start, "RPT_END": self.rpt_end,
             "REQ_ID": self.req_id}[value_cd]
        return str(v) if text and v is not None and not isinstance(v, str) else v


def column_mapping(cfg, rows: Iterable) -> dict[str, str]:
    """{column: value code} for a file config: its table's rows over the project's '*' rows.

    Without any row for the table the framework's own column names apply (DEFAULT_MAPPING).
    """
    chosen: dict = {}
    for r in rows:
        if r.project_cd != cfg.project_cd or r.table_nm not in ("*", cfg.table_nm):
            continue
        if r.column_nm not in chosen or r.table_nm != "*":
            chosen[r.column_nm] = r
    if not chosen:
        return dict(DEFAULT_MAPPING)
    out = {}
    for name, r in chosen.items():
        if not r.active:
            continue
        if r.value_cd not in VALUE_CODES:
            raise ConfigError(f"{r.label}: unknown Value_Cd {r.value_cd!r}; accepted: {', '.join(VALUE_CODES)}")
        if name == BATCH_COLUMN:
            raise ConfigError(f"{r.label}: {BATCH_COLUMN} is always the batch ID and needs no row")
        out[name] = r.value_cd
    return out


@dataclass(frozen=True)
class Layout:
    """The staging and core columns of one file config and how each is filled."""
    label: str
    stage: tuple
    core: tuple
    mapping: dict

    @property
    def file_cols(self) -> list[str]:
        return [c.name for c in self.stage if c.name != BATCH_COLUMN and c.name not in self.mapping]

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

    def stage_rows(self, records: Iterable[list], ctx: LoadContext, sequence_start: int = 1) -> Iterator[list]:
        """Each file record as a full staging row, in staging column order."""
        slots = []
        for c in self.stage:
            value_cd = "BATCH_ID" if c.name == BATCH_COLUMN else self.mapping.get(c.name)
            if value_cd is None:
                slots.append(("file", None, False))
            elif value_cd == "ROW_SEQUENCE":
                slots.append(("seq", None, c.text))
            else:
                slots.append(("value", ctx.value(value_cd, c.text), False))
        for n, rec in enumerate(records):
            it = iter(rec)
            yield [next(it) if kind == "file" else value if kind == "value"
                   else (str(sequence_start + n) if text else sequence_start + n) for kind, value, text in slots]


def build_layout(label: str, stage_cols: list[Column], core_cols: list[Column], mapping: dict, stage_name: str,
                 core_name: str) -> Layout:
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
                                         if k in {c.name for c in stage} | {c.name for c in core}})
    if mapping is not DEFAULT_MAPPING and mapping != DEFAULT_MAPPING:
        missing = sorted(k for k in mapping if k not in layout.mapping)
        if missing:
            raise LookupError(f"{label}: column {missing[0]} of ComplianceTargetColumnConfig is in neither {stage_name} "
                              f"nor {core_name}")
    if layout.column("CURRENT_FLAG", "core") is None:
        raise LookupError(f"core table {core_name} has no column for the current flag (CURRENT_FLAG): the earlier "
                          "load of a batch could not be disabled")
    if layout.column("ROW_SEQUENCE", "core") and not layout.column("ROW_SEQUENCE", "stage"):
        raise LookupError(f"{label}: the ROW_SEQUENCE column must also be in the staging table {stage_name}")
    if not layout.file_cols:
        raise LookupError(f"staging table {stage_name} has no column left for the file's data")
    layout.core_plan()
    return layout
