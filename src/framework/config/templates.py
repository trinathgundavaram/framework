"""Filename templates (design §9).

A template is literal text plus exactly one of each placeholder:
  {PROJECT} {TABLE} {SRC}  -> the config row's aliases, literally (D-31)
  {RUNTY}                  -> [A-Za-z0-9]+, must equal a Run_Ty code exactly (D-31)
  {RPTSTART} {RPTEND}      -> YYYYMMDD (D-32)
  {TS}                     -> YYYYMMDDHHMMSS, uniqueness only (D-36, D-37)
Adjacent placeholders must be separated by literal text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from .models import FileConfig

PLACEHOLDERS = ("PROJECT", "TABLE", "SRC", "RUNTY", "RPTSTART", "RPTEND", "TS")
_TOKEN = re.compile(r"\{([^{}]*)\}")


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class MatchResult:
    cfg: FileConfig
    run_ty: str
    rpt_start: date
    rpt_end: date
    file_ts: datetime


class MatchError(Exception):
    def __init__(self, event_ty: str, message: str, cfg: Optional[FileConfig] = None):
        super().__init__(message)
        self.event_ty = event_ty
        self.cfg = cfg


def parse_template(template: str) -> list[tuple[str, str]]:
    """Split into [('lit', text) | ('ph', NAME)] and validate the grammar."""
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN.finditer(template):
        if m.start() > pos:
            parts.append(("lit", template[pos:m.start()]))
        parts.append(("ph", m.group(1)))
        pos = m.end()
    if pos < len(template):
        parts.append(("lit", template[pos:]))
    if "{" in "".join(t for k, t in parts if k == "lit") or "}" in "".join(t for k, t in parts if k == "lit"):
        raise TemplateError(f"unbalanced braces in template {template!r}")
    names = [t for k, t in parts if k == "ph"]
    unknown = sorted(set(names) - set(PLACEHOLDERS))
    if unknown:
        raise TemplateError(f"unknown placeholder(s) {unknown} in {template!r}")
    for p in PLACEHOLDERS:
        n = names.count(p)
        if n != 1:
            raise TemplateError(f"placeholder {{{p}}} must appear exactly once in {template!r} (found {n})")
    for a, b in zip(parts, parts[1:]):
        if a[0] == "ph" and b[0] == "ph":
            raise TemplateError(f"placeholders {{{a[1]}}}{{{b[1]}}} must be separated by literal text in {template!r}")
    if "/" in template:
        raise TemplateError(f"template must describe a file name only (no '/'): {template!r}")
    return parts


def compile_template(template: str, project_alias: str, table_alias: str, src_alias: str,
                     case_sensitive: bool = True) -> re.Pattern:
    aliases = {"PROJECT": project_alias, "TABLE": table_alias, "SRC": src_alias}
    out = []
    for kind, text in parse_template(template):
        if kind == "lit":
            out.append(re.escape(text))
        elif text in aliases:
            out.append(f"(?P<{text.lower()}>{re.escape(aliases[text])})")
        elif text == "RUNTY":
            out.append(r"(?P<runty>[A-Za-z0-9]+)")
        elif text in ("RPTSTART", "RPTEND"):
            out.append(rf"(?P<{text.lower()}>\d{{8}})")
        elif text == "TS":
            out.append(r"(?P<ts>\d{14})")
    return re.compile("".join(out), 0 if case_sensitive else re.IGNORECASE)


def render(template: str, *, project: str, table: str, src: str, runty: str,
           rpt_start: date, rpt_end: date, ts: datetime) -> str:
    """Build a file name from a template (used by validator overlap tests and by tests)."""
    values = {"PROJECT": project, "TABLE": table, "SRC": src, "RUNTY": runty,
              "RPTSTART": f"{rpt_start:%Y%m%d}", "RPTEND": f"{rpt_end:%Y%m%d}", "TS": f"{ts:%Y%m%d%H%M%S}"}
    return "".join(values[t] if k == "ph" else t for k, t in parse_template(template))


class TemplateMatcher:
    def __init__(self, configs: Iterable[FileConfig], case_sensitive: bool = True):
        self.case_sensitive = case_sensitive
        self._compiled: list[tuple[FileConfig, re.Pattern]] = []
        self.invalid: list[tuple[FileConfig, str]] = []
        for c in configs:
            try:
                self._compiled.append((c, compile_template(c.src_file_nm_tmplt, c.project_alias,
                                                           c.table_alias, c.src_alias, case_sensitive)))
            except TemplateError as e:
                self.invalid.append((c, str(e)))

    @property
    def configs(self) -> Sequence[FileConfig]:
        return [c for c, _ in self._compiled]

    def candidates(self, name: str) -> list[tuple[FileConfig, re.Match]]:
        out = []
        for c, rx in self._compiled:
            m = rx.fullmatch(name)
            if m:
                out.append((c, m))
        return out

    def match(self, name: str) -> MatchResult:
        found = self.candidates(name)
        if not found:
            raise MatchError("FILE_REJECTED_UNPARSEABLE", f"{name} matches no active filename template")
        if len(found) > 1:
            ids = [c.cfg_id for c, _ in found]
            raise MatchError("FILE_REJECTED_AMBIGUOUS_TEMPLATE", f"{name} matches several templates (Cfg_ID {ids})")
        cfg, m = found[0]
        try:
            start = datetime.strptime(m.group("rptstart"), "%Y%m%d").date()
            end = datetime.strptime(m.group("rptend"), "%Y%m%d").date()
        except ValueError:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: report dates are not valid YYYYMMDD", cfg)
        if end < start:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: report end date is before start date", cfg)
        try:
            ts = datetime.strptime(m.group("ts"), "%Y%m%d%H%M%S")
        except ValueError:
            raise MatchError("FILE_REJECTED_INVALID_TOKEN", f"{name}: {{TS}} is not a valid YYYYMMDDHHMMSS", cfg)
        return MatchResult(cfg, m.group("runty"), start, end, ts)
