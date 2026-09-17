"""Structural file reading (design §9.4 C12-C14, D-58, D-59). Values are returned as text;
Postgres performs the typed cast during staging (a cast failure -> FILE_PARSE_ERROR)."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Iterator, Optional

from ..config.models import FileConfig
from ..errors import ConfigError, FileRejected
from ..settings import Settings

_DELIMS = {"TAB": "\t", "\\T": "\t", "PIPE": "|", "COMMA": ",", "SEMICOLON": ";"}
_STD_TERMINATORS = {None, "", "\\N", "\\R\\N", "LF", "CRLF", "\n", "\r\n"}


@dataclass
class ReadResult:
    rows: list[list[Optional[str]]]
    header_col_count: Optional[int]
    trailer_count: Optional[int]

    @property
    def data_row_count(self) -> int:
        return len(self.rows)


def delimiter_for(cfg: FileConfig) -> str:
    d = cfg.delmtr_cd
    if d is None or d == "":
        return ","
    return _DELIMS.get(d.upper(), d)


def _check_terminator(cfg: FileConfig) -> None:
    lt = cfg.line_term_cd
    if lt is not None and lt.upper() not in _STD_TERMINATORS and lt not in _STD_TERMINATORS:
        raise ConfigError(f"Line_Term_Cd {lt!r} is not supported (open question Q-02)")


def sanitize_db_error(msg: str) -> str:
    """Remove quoted values from database error text so no file content reaches audit logs."""
    return re.sub(r'"[^"]*"', '"***"', msg or "")[:500]


def _normalise(values: list[str], empty_as_null: bool) -> list[Optional[str]]:
    return [None if (empty_as_null and v == "") else v for v in values]


def read_delimited(path: str, cfg: FileConfig, expected_cols: int, settings: Settings) -> ReadResult:
    _check_terminator(cfg)
    delim = delimiter_for(cfg)
    try:
        with open(path, "r", encoding=settings.file_encoding, newline="") as f:
            text = f.read()
    except UnicodeDecodeError as e:
        raise FileRejected("FILE_PARSE_ERROR", f"file is not valid {settings.file_encoding}: {e.reason}")
    lines = text.splitlines(keepends=True)
    while lines and lines[-1].strip() == "":
        lines.pop()
    header_cols = None
    trailer_count = None
    if cfg.has_trailer:
        if not lines:
            raise FileRejected("FILE_PARSE_ERROR", "trailer expected but file is empty")
        trailer = lines.pop()
        if settings.trailer_count_check:
            m = re.search(settings.trailer_count_regex, trailer)
            if not m:
                raise FileRejected("FILE_TRAILER_COUNT_MISMATCH", "trailer record count not found")
            trailer_count = int(m.group(1))
    reader = csv.reader(io.StringIO("".join(lines)), delimiter=delim, quotechar=settings.quote_char or None,
                        strict=True)
    rows: list[list[Optional[str]]] = []
    try:
        for i, rec in enumerate(reader):
            if i == 0 and cfg.has_header:
                header_cols = len(rec)
                continue
            if len(rec) != expected_cols:
                raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                   f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols}")
            rows.append(_normalise(rec, settings.empty_as_null))
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file at line {reader.line_num}: {e}")
    if cfg.has_header and header_cols is None:
        raise FileRejected("FILE_PARSE_ERROR", "header expected but file has no lines")
    if trailer_count is not None and trailer_count != len(rows):
        raise FileRejected("FILE_TRAILER_COUNT_MISMATCH",
                           f"trailer count {trailer_count} != data rows {len(rows)}")
    return ReadResult(rows, header_cols, trailer_count)


def scan_delimited(path: str, cfg: FileConfig, expected_cols: int, settings: Settings) -> int:
    """Streaming structural check (no rows kept in memory). Returns the data row count."""
    _check_terminator(cfg)
    if cfg.has_trailer:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "streaming scan does not support trailer records yet (Q-02)")
    count = 0
    try:
        with open(path, "r", encoding=settings.file_encoding, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter_for(cfg), quotechar=settings.quote_char or None, strict=True)
            for i, rec in enumerate(reader):
                if i == 0 and cfg.has_header:
                    continue
                if not rec:
                    continue
                if len(rec) != expected_cols:
                    raise FileRejected("FILE_COLUMN_COUNT_MISMATCH",
                                       f"line {reader.line_num}: {len(rec)} columns, expected {expected_cols}")
                count += 1
    except UnicodeDecodeError as e:
        raise FileRejected("FILE_PARSE_ERROR", f"file is not valid {settings.file_encoding}: {e.reason}")
    except csv.Error as e:
        raise FileRejected("FILE_PARSE_ERROR", f"malformed delimited file: {e}")
    return count


def read_with_pandas(path: str, cfg: FileConfig, expected_cols: int, settings: Settings) -> ReadResult:
    import pandas as pd  # optional dependency

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
    rows = [_normalise([str(v) for v in rec], settings.empty_as_null) for rec in df.itertuples(index=False)]
    return ReadResult(rows, None, None)


def read_file(path: str, cfg: FileConfig, expected_cols: int, settings: Settings) -> ReadResult:
    ft = cfg.src_file_ty.lower()
    if ft not in settings.supported_file_types:
        raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} is not enabled (Q-02)")
    if ft in (".txt", ".csv", ".dat", ".psv", ".tsv"):
        return read_delimited(path, cfg, expected_cols, settings)
    if ft in (".xlsx", ".xls", ".parquet"):
        return read_with_pandas(path, cfg, expected_cols, settings)
    raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"file type {ft} has no reader")


def iter_chunks(rows: list, size: int) -> Iterator[list]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]
