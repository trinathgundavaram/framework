"""Which content checks a file gets (ComplianceFileCheckConfig); read by FILE_CHECK and by the load."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Optional

from .common import ConfigError

TRAILER_FIELDS = ("RECORD_TYPE", "FILE_NAME", "DATA_ROW_COUNT", "TOTAL_ROW_COUNT", "TIMESTAMP", "SKIP")
CHECKS = ("RECORD_TYPE", "HEADER_NAMES", "TRAILER", "CHARACTER_SET", "COLUMN_VALUES")
VALUE_RULES = ("LENGTH", "NUMBER", "REQUIRED")


@dataclass(frozen=True)
class CheckOptions:
    record_types: Optional[tuple[str, str, str]] = None
    trailer_fields: tuple[str, ...] = ()
    header: Optional[str] = None
    charset: Optional[str] = None
    value_rules: tuple = ()


def _names(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(i).strip() for i in items if str(i).strip()]


def check_options(cfg, checks) -> CheckOptions:
    """The content checks of a file config, from the ComplianceFileCheckConfig rows.

    For each Check_Cd the most specific row of the file's project wins (its table and source, then its table,
    then '*'); an inactive row switches that check off for the files it covers.
    """
    chosen: dict = {}
    for k in checks:
        if k.project_cd != cfg.project_cd or k.table_nm not in ("*", cfg.table_nm) or k.src_id not in ("*", cfg.src_id):
            continue
        rank = 2 * (k.table_nm != "*") + (k.src_id != "*")
        if k.check_cd not in chosen or rank > chosen[k.check_cd][0]:
            chosen[k.check_cd] = (rank, k)
    values = {}
    for check_cd, (_, k) in chosen.items():
        if not k.active or check_cd not in CHECKS:
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
    return CheckOptions(tuple(types) if types else None, fields, header, charset, rules)
