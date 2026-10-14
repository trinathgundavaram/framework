"""Which content checks a file gets (ComplianceFileCheckConfig); read by FILE_CHECK and by the load."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .common import ConfigError

TRAILER_FIELDS = ("RECORD_TYPE", "FILE_NAME", "DATA_ROW_COUNT", "TOTAL_ROW_COUNT", "TIMESTAMP", "SKIP")
CHECKS = ("RECORD_TYPE", "HEADER_NAMES", "COLUMN_COUNT", "TRAILER", "CHARACTER_SET", "COLUMN_VALUES")
VALUE_RULES = ("LENGTH", "NUMBER", "REQUIRED")
COUNT_FIELDS = ("DATA_ROW_COUNT", "TOTAL_ROW_COUNT")
_TIMESTAMP_FORMATS = {8: "%Y%m%d", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}


@dataclass(frozen=True)
class CheckOptions:
    record_types: Optional[tuple[str, str, str]] = None
    trailer_fields: tuple[str, ...] = ()
    header: Optional[str] = None
    charset: Optional[str] = None
    value_rules: tuple = ()
    column_count: Optional[int] = None


def _names(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(i).strip() for i in items if str(i).strip()]


def check_options(cfg, checks) -> CheckOptions:
    """The content checks of a file config, from the ComplianceFileCheckConfig rows.

    Inactive rows are ignored. For each Check_Cd the most specific active row of the file's project wins: its
    table and source, then its table, then its source, then '*'. Include_Ind of that row says whether the
    check applies (1) or is excluded (0) for the file.
    """
    chosen: dict = {}
    for k in checks:
        if (not k.active or k.project_cd != cfg.project_cd or k.table_nm not in ("*", cfg.table_nm)
                or k.src_id not in ("*", cfg.src_id)):
            continue
        rank = 2 * (k.table_nm != "*") + (k.src_id != "*")
        if k.check_cd not in chosen or rank > chosen[k.check_cd][0]:
            chosen[k.check_cd] = (rank, k)
    values = {}
    for check_cd, (_, k) in chosen.items():
        if not k.include or check_cd not in CHECKS:
            continue
        if not k.check_val_txt:
            raise ConfigError(f"{k.label}: Check_Val_Txt is empty")
        values[check_cd] = k
    types = _names(values["RECORD_TYPE"].check_val_txt) if "RECORD_TYPE" in values else []
    if types and len(types) != 3:
        raise ConfigError(f"{values['RECORD_TYPE'].label}: Check_Val_Txt must be three values: header, detail, trailer "
                          "(for example H,D,T)")
    fields = tuple(f.upper() for f in _names(values["TRAILER"].check_val_txt)) if "TRAILER" in values else ()
    bad = [f for f in fields if f not in TRAILER_FIELDS]
    if bad:
        raise ConfigError(f"{values['TRAILER'].label}: Check_Val_Txt has unknown value {bad[0]!r}; "
                          f"accepted: {', '.join(TRAILER_FIELDS)}")
    header = values["HEADER_NAMES"].check_val_txt.strip() if "HEADER_NAMES" in values else None
    charset = values["CHARACTER_SET"].check_val_txt.strip() if "CHARACTER_SET" in values else None
    if charset:
        try:
            codecs.lookup(charset)
        except LookupError:
            raise ConfigError(f"{values['CHARACTER_SET'].label}: Check_Val_Txt {charset!r} is not a character set "
                              "(for example latin-1, ascii, cp1252)") from None
    rules = tuple(r.upper() for r in _names(values["COLUMN_VALUES"].check_val_txt)) if "COLUMN_VALUES" in values else ()
    if rules == ("ALL",):
        rules = VALUE_RULES
    bad = [r for r in rules if r not in VALUE_RULES]
    if bad:
        raise ConfigError(f"{values['COLUMN_VALUES'].label}: Check_Val_Txt has unknown value {bad[0]!r}; "
                          f"accepted: ALL, {', '.join(VALUE_RULES)}")
    count = None
    if "COLUMN_COUNT" in values:
        text = values["COLUMN_COUNT"].check_val_txt.strip()
        if not text.isdigit() or int(text) < 1:
            raise ConfigError(f"{values['COLUMN_COUNT'].label}: Check_Val_Txt must be the number of fields in a row "
                              "(for example 23)")
        count = int(text)
    return CheckOptions(tuple(types) if types else None, fields, header, charset, rules, count)


def data_fields(opts: CheckOptions, names: tuple) -> Optional[int]:
    """Fields per row the file must have (record type included): COLUMN_COUNT, else HEADER_NAMES; None = not set."""
    if opts.column_count:
        return opts.column_count
    return len(names) + (1 if opts.record_types else 0) if names else None


def _is_timestamp(value: str) -> bool:
    fmt = _TIMESTAMP_FORMATS.get(len(value))
    if not fmt or not value.isdigit():
        return False
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


def trailer_problems(rec: list, line_no: int, name: str, opts: CheckOptions, case_sensitive: bool, data_rows: int,
                     lines: int) -> list[tuple[str, str]]:
    """[(check, message)] of a trailer record against the file's TRAILER and RECORD_TYPE rows.

    `data_rows` = detail records, `lines` = every non-blank line including the header and the trailer.
    """
    out = []
    trailer_code = opts.record_types[2] if opts.record_types else None
    if trailer_code and rec[0] != trailer_code:
        out.append(("RECORD_TYPE", f"line {line_no}: trailer record type {rec[0]!r}, expected {trailer_code!r}"))
    if not opts.trailer_fields:
        return out
    if len(rec) != len(opts.trailer_fields):
        out.append(("TRAILER", f"line {line_no}: trailer has {len(rec)} fields, expected "
                               f"{len(opts.trailer_fields)} ({', '.join(opts.trailer_fields)})"))
    for value, kind in zip(rec, opts.trailer_fields):
        value = value.strip()
        if kind == "FILE_NAME":
            same = value == name if case_sensitive else value.casefold() == name.casefold()
            if not same:
                out.append(("TRAILER", f"line {line_no}: trailer file name {value!r} is not the name of the file"))
        elif kind in COUNT_FIELDS:
            actual, unit = (data_rows, "data rows") if kind == "DATA_ROW_COUNT" else (lines, "lines")
            if not value.isdigit() or int(value) != actual:
                out.append(("TRAILER", f"line {line_no}: trailer count {value!r}, the file has {actual} {unit}"))
        elif kind == "TIMESTAMP" and not _is_timestamp(value):
            out.append(("TRAILER", f"line {line_no}: trailer timestamp {value!r} is not a valid "
                                   "YYYYMMDD, YYYYMMDDHHMM or YYYYMMDDHHMMSS"))
    return out
