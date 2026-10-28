"""EXPORT_LOAD, FILE_RULES and the bridge to the GRE rules engine."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from compliance_framework import config as cfgmod, db
from compliance_framework.adapters import ERROR, RuleEngine
from compliance_framework.audit import EventLogger
from compliance_framework.batches import Releases, get_batch
from compliance_framework.common import Clock, ConfigError, code
from compliance_framework.config import Scope
from compliance_framework.db import Settings, ident
from compliance_framework.load import (BATCH_COLUMN, LoadContext, _cast, _converted, batch_column, column_mapping, columns,
    export_plan, sanitize_db_error)


# export: EXPORT_LOAD: copy each batch whose data changed from core into its export table (ComplianceExportConfig).

log = logging.getLogger(__name__)

CHANGES = ("FILE_LOADED", "LATE_ARRIVAL_LOADED", "CORRECTION_LOADED", "CARRY_FORWARD_APPLIED",
           "CARRY_FORWARD_REMOVED")
BATCH_VALUES = ("RUN_TYPE", "VERSION", "REQ_ID", "RPT_START", "RPT_END")


@dataclass
class ExportSummary:
    tables: list[str] = field(default_factory=list)
    batches: list[dict] = field(default_factory=list)
    exported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)
    released: list[str] = field(default_factory=list)


def _as_date(value):
    return value.date() if hasattr(value, "hour") else value


class ExportLoader:
    def __init__(self, conn, clock, settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)
        self._hold: tuple = ()

    def run(self, scope: Optional[Scope] = None, batches: Optional[list[str]] = None,
            release: bool = False, hold: tuple = ()) -> ExportSummary:
        """Export the scope's batches whose data changed since their last export, or the batches named. Batches of
        scheduled run types wait until due, ad-hoc ones until released, and run types in `hold` until released, unless `release`
        releases the ones with data now."""
        scope = scope or Scope()
        exports = {(e.project_cd, e.table_nm): e for e in cfgmod.active_export_configs(self.conn, self.settings)
                   if scope.covers(e.project_cd, e.table_nm)}
        s = ExportSummary(tables=sorted(f"{e.exp_schema_nm}.{e.exp_table_nm}" for e in exports.values()))
        self._hold = hold
        todo = self._held(self._named(exports, scope, batches, s) if batches else self._changed(exports, scope),
                          s, release, bool(batches))
        log.info("EXPORT_LOAD | %s | %s batch(es) to export%s", scope.label(), len(todo),
                 ": " + ", ".join(b["btch_id"] for b in todo[:50]) if todo else "")
        groups: dict[str, list[dict]] = {}
        for b in todo:
            groups.setdefault(b["btch_id"], []).append(b)
        for rows in groups.values():
            e = exports[(rows[0]["project_cd"], rows[0]["table_nm"])]
            if e.pre_sql or e.post_sql:
                self._export_with_steps(e, rows, s)
                continue
            for b in rows:
                try:
                    with db.held_all(self.conn, [db.req_key(b), self._table_key(e)], self.settings.lock_timeout_seconds):
                        item = self._export(e, b)
                except Exception as err:  # noqa: BLE001
                    item = self._failed(b, err, s)
                self._count(item, s)
        return s

    @staticmethod
    def _table_key(e) -> str:
        return db.batch_key(f"EXPORT {e.exp_schema_nm}.{e.exp_table_nm}".upper())

    def _export_with_steps(self, e, rows: list[dict], s: ExportSummary) -> None:
        """A batch with Exp_Pre_Sql_Txt / Exp_Post_Sql_Txt: pre, every source's export, post, as one transaction; a
        failure rolls the whole batch back (every source FAILED)."""
        b = rows[0]
        params = {"btch_id": b["btch_id"], "exp_btch_id": b["btch_id"], "project_cd": b["project_cd"],
                  "table_nm": b["table_nm"], "run_ty": b["run_ty"], "version": b["cmplnc_vrsn"],
                  "req_dt": _as_date(b["req_dt_key"]), "rpt_start": _as_date(b["rpt_start_dt_key"]),
                  "rpt_end": _as_date(b["rpt_end_dt_key"]), "exp_schema_nm": e.exp_schema_nm,
                  "exp_table_nm": e.exp_table_nm}
        items = []
        try:
            with db.held_all(self.conn, [*(db.req_key(r) for r in rows), self._table_key(e)],
                             self.settings.lock_timeout_seconds), self.conn.transaction():
                if e.pre_sql:
                    self._step(e, "Exp_Pre_Sql_Txt", e.pre_sql, params)
                items = [self._export(e, r) for r in rows]
                if e.post_sql:
                    self._step(e, "Exp_Post_Sql_Txt", e.post_sql, params)
        except Exception as err:  # noqa: BLE001
            items = [self._failed(r, err, s) for r in rows]
        for item in items:
            self._count(item, s)

    def _step(self, e, what: str, text: str, params: dict) -> None:
        body = cfgmod.export_statement(f"{e.label}: {what}", text, cfgmod.EXPORT_BATCH_PARAMS)
        names = [n.lower() for n in cfgmod.EXPORT_SQL_PLACEHOLDER.findall(body)]
        db.log_sql(f"EXPORT_LOAD | {params['btch_id']} | {what}", body, params)
        n = self.conn.execute_qmark(cfgmod.EXPORT_SQL_PLACEHOLDER.sub("?", body), [params[x] for x in names]).rowcount
        log.info("EXPORT_LOAD | %s | %s done (%s row(s))", params["btch_id"], what, n if n is not None and n >= 0 else "-")

    def _failed(self, b: dict, err: Exception, s: ExportSummary) -> dict:
        s.errors.append(f"{b['btch_id']} source {b['src_id']}: {db.error_text(err)}")
        with self.conn.transaction():
            self.logger.audit("EXPORT_LOAD_FAILED", description=db.error_text(err), project_cd=b["project_cd"],
                              table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"],
                              req_id=b["req_id"], btch_id=b["btch_id"])
        return {"batch": b["btch_id"], "source": b["src_id"], "status": "FAILED", "detail": db.error_text(err)}

    @staticmethod
    def _count(item: dict, s: ExportSummary) -> None:
        s.batches.append(item)
        if item["status"] == "EXPORTED":
            s.exported += 1
        elif item["status"] == "SKIPPED":
            s.skipped += 1
        elif item["status"] == "FAILED":
            s.failed += 1

    def _held(self, todo: list[dict], s: ExportSummary, release: bool, named: bool) -> list[dict]:
        """The batches that may be exported now; named batches always are. `release` releases held batches with data."""
        if not todo:
            return todo
        r = Releases(self.conn, self.clock, self.settings, self._hold)
        ready, held = r.split(todo)
        freed = [b for b in held if b["resolution_ty"] is not None] if release else []
        if freed:
            r.release(freed, "EXPORT_LOAD")
            s.released = [b["btch_id"] for b in freed]
        if named:
            return todo
        held = [b for b in held if b not in freed]
        s.held = [{"batch": b["btch_id"], "hold": b["hold"]} for b in held]
        if held:
            log.info("EXPORT_LOAD | %s batch(es) held: %s", len(held), ", ".join(b["btch_id"] for b in held[:50]))
        go = {b["req_id"] for b in ready} | {b["req_id"] for b in freed}
        return [b for b in todo if b["req_id"] in go]

    def _named(self, exports: dict, scope: Scope, names: list[str], s: ExportSummary) -> list[dict]:
        out = []
        for name in dict.fromkeys(code(n) for n in names):
            rows = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID = %s ORDER BY Req_ID",
                                     (code(name),)).fetchall()
            found = [b for b in rows if (b["project_cd"], b["table_nm"]) in exports
                     and scope.covers(b["project_cd"], b["table_nm"], b["run_ty"], b["src_id"])]
            if not rows:
                s.errors.append(f"{name}: no such batch")
            elif not found:
                s.errors.append(f"{name}: its table has no export table in {scope.label()}")
            out.extend(found)
        return out

    def _changed(self, exports: dict, scope: Scope) -> list[dict]:
        """Batches whose data (or the data of the batch they reuse) changed after their last export."""
        if not exports:
            return []
        where, params = scope.sql("c")
        marks, changes = db.in_list(CHANGES)
        rows = self.conn.execute(
            f"""SELECT c.* FROM ComplianceRequestControl c
                  JOIN (SELECT Req_ID, MAX(Event_Dtts) AS changed_at FROM ComplianceRequestFileDetail
                         WHERE Event_Ty IN ({marks}) GROUP BY Req_ID) ch ON ch.Req_ID = c.Req_ID
                  LEFT JOIN ComplianceRequestControl r ON r.Btch_ID = c.Reuse_Btch_ID AND r.Src_ID = c.Src_ID
                  LEFT JOIN (SELECT Req_ID, MAX(Event_Dtts) AS changed_at FROM ComplianceRequestFileDetail
                              WHERE Event_Ty IN ({marks}) GROUP BY Req_ID) rch ON rch.Req_ID = r.Req_ID
                  LEFT JOIN (SELECT Req_ID, MAX(Event_Dtts) AS exported_at FROM ComplianceRequestFileDetail
                              WHERE Event_Ty = 'EXPORT_LOADED' GROUP BY Req_ID) ex ON ex.Req_ID = c.Req_ID
                 WHERE (ex.exported_at IS NULL OR ch.changed_at > ex.exported_at
                        OR (c.Resolution_Ty = 'CARRY_FORWARD' AND rch.changed_at > ex.exported_at)){where}
                 ORDER BY c.Req_ID""", [*changes, *changes, *params]).fetchall()
        return [b for b in rows if (b["project_cd"], b["table_nm"]) in exports]

    def _data_chain(self, b: dict) -> list[str]:
        """This batch and the batches it reuses, ending with the one whose core rows are its data; [] = no data."""
        chain = []
        while b is not None and b["btch_id"] not in chain:
            chain.append(b["btch_id"])
            if b["resolution_ty"] == "NEW_FILE":
                return chain
            if b["resolution_ty"] != "CARRY_FORWARD" or not b["reuse_btch_id"]:
                return []
            b = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID = %s AND Src_ID = %s",
                                  (b["reuse_btch_id"], b["src_id"])).fetchone()
        return []

    def _export(self, e, b: dict) -> dict:
        b = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID = %s", (b["req_id"],)).fetchone()
        item = {"batch": b["btch_id"], "source": b["src_id"], "run_type": b["run_ty"],
                "report_dates": f"{_as_date(b['rpt_start_dt_key'])} to {_as_date(b['rpt_end_dt_key'])}",
                "batch_status": b["req_stat"], "export_table": f"{e.exp_schema_nm}.{e.exp_table_nm}"}
        period = (b["project_cd"], b["table_nm"], b["src_id"], b["run_ty"], b["rpt_start_dt_key"], b["rpt_end_dt_key"])
        newest = self.conn.execute(
            """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
                  AND Run_Ty=%s AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s
                  AND Resolution_Ty IN ('NEW_FILE', 'CARRY_FORWARD', 'NO_DATA')
                ORDER BY Req_ID DESC""", period).fetchone()
        keep = self._data_chain(newest) if newest else []
        if newest and newest["btch_id"] != b["btch_id"] and b["btch_id"] not in keep:
            item.update(status="SKIPPED", detail=f"the period's data is batch {newest['btch_id']} (the latest batch of the "
                                                 "period with data or 'no data' from the source)")
            self._done(b, f"not exported: {item['detail']}")
            log.info("EXPORT_LOAD | %s | skipped: %s", b["btch_id"], item["detail"])
            return item
        cfg = cfgmod.target_tables(self.conn, b["project_cd"], b["table_nm"], b["src_id"], self.settings)
        if cfg is None:
            raise LookupError(f"{b['project_cd']}/{b['table_nm']}/{b['src_id']} has no file or query config")
        chain = self._data_chain(b)
        source = chain[-1] if chain else None
        export = db.qualified(e.exp_schema_nm, e.exp_table_nm)
        exp_btch_id, version = b["btch_id"], b["cmplnc_vrsn"]
        mapping = column_mapping(cfg, cfgmod.target_columns(self.conn))
        ctx = LoadContext(b["btch_id"], 0, b["run_ty"], version, b["src_id"], "", self.clock.now(), b["project_cd"],
                          b["table_nm"], _as_date(b["rpt_start_dt_key"]), _as_date(b["rpt_end_dt_key"]), b["req_id"],
                          self.settings.business_tz)
        exp_all = columns(self.conn, e.exp_schema_nm, e.exp_table_nm)
        src_col = next((c for c in exp_all if mapping.get(c.name) == "SOURCE_ID"), None)
        if src_col is None:
            raise LookupError(f"export table {e.exp_schema_nm}.{e.exp_table_nm} lacks the column mapped to SOURCE_ID")
        by_src = f"{db.ident(src_col.name)} = ?"
        src_val = ctx.value("SOURCE_ID", src_col.text, src_col.temporal)
        exp_batch = batch_column((c.name for c in exp_all if not c.auto), mapping)
        if exp_batch is None:
            raise LookupError(f"export table {e.exp_schema_nm}.{e.exp_table_nm} lacks the batch column {BATCH_COLUMN} "
                              "(or a column mapped to BATCH_ID)")
        if e.proc_nm:
            return self._call(e, b, item, export, exp_btch_id, version, chain, by_src, src_val, exp_batch)
        if e.sql_txt:
            return self._select(e, b, item, export, exp_all, chain, by_src, src_val, src_col, exp_batch, period, keep)
        core = db.qualified(cfg.core_schema_nm, cfg.core_table_nm)
        core_all = [c for c in columns(self.conn, cfg.core_schema_nm, cfg.core_table_nm) if not c.auto]
        core_batch = batch_column((c.name for c in core_all), mapping)
        core_src = next((c for c in core_all if mapping.get(c.name) == "SOURCE_ID"), None)
        if core_batch is None or core_src is None:
            raise LookupError(f"core table {cfg.core_schema_nm}.{cfg.core_table_nm} lacks the batch or the source column")
        plan = export_plan([c for c in exp_all if not c.auto], core_all, mapping, exp_batch, e.exp_btch_col, core_batch)
        exp_cols, exprs, values = [], [], []
        for c, s_name in plan:
            value_cd = "BATCH_ID" if c.name in (BATCH_COLUMN, exp_batch) else mapping.get(c.name)
            if c.name == e.exp_btch_col:
                expr, v = _cast(c, exp_btch_id)
            elif source != b["btch_id"] and (value_cd == "BATCH_ID" or value_cd in BATCH_VALUES):
                expr, v = _cast(c, ctx.value(value_cd, c.text, c.temporal))
            elif s_name is None:
                continue
            else:
                s_col = next(x for x in core_all if x.name == s_name)
                expr, v = (_converted(c, s_name) if s_col.text and not c.text and c.cast else db.ident(s_name)), []
            exp_cols.append(c)
            exprs.append(expr)
            values.extend(v)
        core_src_val = ctx.value("SOURCE_ID", core_src.text, core_src.temporal)
        with self.conn.transaction():
            kept = [k for k in keep if k != b["btch_id"]]
            marks, kept = db.in_list(kept) if kept else ("", [])
            deleted = self.conn.execute_qmark(
                f"""DELETE FROM {export} WHERE {by_src} AND {db.ident(exp_batch)} IN (
                      SELECT Btch_ID FROM ComplianceRequestControl WHERE Project_Cd=? AND Table_Nm=? AND Src_ID=?
                         AND Run_Ty=? AND Rpt_Start_Dt_Key=? AND Rpt_End_Dt_Key=?)"""
                + (f" AND {db.ident(exp_batch)} NOT IN ({marks.replace('%s', '?')})" if kept else ""),
                [src_val, *period, *kept]).rowcount
            inserted = 0
            if source is not None:
                text = (f"INSERT INTO {export} ({', '.join(db.ident(c.name) for c in exp_cols)}) "
                        f"SELECT {', '.join(exprs)} FROM {core} WHERE {db.ident(core_batch)} = ? "
                        f"AND {db.ident(core_src.name)} = ?")
                inserted = self.conn.execute_qmark(text, [*values, source, core_src_val]).rowcount
            how = ("no data" if source is None else "its own rows" if source == b["btch_id"]
                   else f"a copy of {source}")
            self._done(b, f"exported {how}: inserted={inserted} deleted={deleted}")
        item.update(status="EXPORTED", export_batch=exp_btch_id, data_from=source or "-", rows_exported=inserted, rows_removed=deleted)
        log.info("EXPORT_LOAD | %s | %s row(s) exported to %s (%s), %s earlier row(s) of the period removed",
                 b["btch_id"], inserted, item["export_table"], how, deleted)
        return item

    def _call(self, e, b: dict, item: dict, export: str, exp_btch_id: str, version, chain: list[str], by_src: str,
              src_val, exp_batch: str = BATCH_COLUMN) -> dict:
        """The configured procedure exports the batch (deletes and inserts its export rows itself)."""
        unknown = [n for n in e.proc_params if n not in cfgmod.EXPORT_PROC_PARAMS]
        if unknown:
            raise ConfigError(f"{e.label}: Exp_Proc_Parm_Txt uses {', '.join(unknown)}")
        source = chain[-1] if chain else None
        given = {"btch_id": b["btch_id"], "data_btch_id": source, "exp_btch_id": exp_btch_id,
                 "reused_btch_ids": ",".join(chain[1:]) or None, "project_cd": b["project_cd"], "table_nm": b["table_nm"],
                 "src_id": b["src_id"], "run_ty": b["run_ty"], "version": version, "req_id": b["req_id"],
                 "req_dt": _as_date(b["req_dt_key"]), "rpt_start": _as_date(b["rpt_start_dt_key"]),
                 "rpt_end": _as_date(b["rpt_end_dt_key"])}
        proc = ".".join(db.ident(p) for p in e.proc_nm.split("."))
        db.log_sql(f"EXPORT_LOAD | {b['btch_id']} source {b['src_id']} | procedure {e.proc_nm}",
                   f"CALL {proc}({', '.join('{' + n + '}' for n in e.proc_params)})", given)
        with self.conn.transaction():
            self.conn.execute_qmark(f"CALL {proc}({', '.join('?' for _ in e.proc_params)})",
                                    [given[n] for n in e.proc_params])
            rows = self.conn.execute_qmark(f"SELECT COUNT(*) AS n FROM {export} WHERE {db.ident(exp_batch)} = ? "
                                           f"AND {by_src}", [b["btch_id"], src_val]).fetchone()["n"]
            self._done(b, f"exported by {e.proc_nm} (data of {source or 'no batch'}): {rows} row(s) of the batch in the export")
        item.update(status="EXPORTED", export_batch=exp_btch_id, data_from=source or "-", procedure=e.proc_nm,
                    rows_exported=rows)
        log.info("EXPORT_LOAD | %s | exported by procedure %s: %s row(s) of the batch in %s", b["btch_id"], e.proc_nm,
                 rows, item["export_table"])
        return item

    def _select(self, e, b: dict, item: dict, export: str, exp_all: list, chain: list[str], by_src: str, src_val,
                src_col, exp_batch: str, period: tuple, keep: list[str]) -> dict:
        """Exp_Sql_Txt exports the batch: the source's earlier export rows of the period are removed and the SELECT's
        rows inserted (its columns named after export columns); the batch, Exp_Btch_ID and source columns are always
        the framework's."""
        source = chain[-1] if chain else None
        given = {"btch_id": b["btch_id"], "data_btch_id": source, "exp_btch_id": b["btch_id"],
                 "reused_btch_ids": ",".join(chain[1:]) or None, "project_cd": b["project_cd"], "table_nm": b["table_nm"],
                 "src_id": b["src_id"], "run_ty": b["run_ty"], "version": b["cmplnc_vrsn"], "req_id": b["req_id"],
                 "req_dt": _as_date(b["req_dt_key"]), "rpt_start": _as_date(b["rpt_start_dt_key"]),
                 "rpt_end": _as_date(b["rpt_end_dt_key"])}
        body = cfgmod.export_statement(f"{e.label}: Exp_Sql_Txt", e.sql_txt, cfgmod.EXPORT_PROC_PARAMS, select=True)
        names = [n.lower() for n in cfgmod.EXPORT_SQL_PLACEHOLDER.findall(body)]
        query, values = cfgmod.EXPORT_SQL_PLACEHOLDER.sub("?", body), [given[n] for n in names]
        db.log_sql(f"EXPORT_LOAD | {b['btch_id']} source {b['src_id']} | Exp_Sql_Txt", body, given)
        cols = {c.name.casefold(): c for c in exp_all if not c.auto}
        fixed = {exp_batch.casefold(): b["btch_id"], e.exp_btch_col.casefold(): b["btch_id"], src_col.name.casefold(): None}
        returned = self.conn.execute_qmark(f"SELECT * FROM ({query}) AS q WHERE 1 = 0", values).columns
        twice = sorted({n for n in returned if [x.casefold() for x in returned].count(n.casefold()) > 1})
        unknown = [n for n in returned if n.casefold() not in cols]
        if twice or unknown:
            raise ConfigError(f"{e.label}: Exp_Sql_Txt returns " + (f"a column name more than once: {', '.join(twice)}"
                              if twice else f"columns that are not in {export}: {', '.join(unknown[:10])}"))
        names_out, exprs, params = [], [], []
        for n in returned:
            if n.casefold() not in fixed:
                names_out.append(db.ident(cols[n.casefold()].name))
                exprs.append(f'q."{n}"')
        for key, value in fixed.items():
            if key in cols:
                c = cols[key]
                expr, v = _cast(c, src_val if key == src_col.name.casefold() else value)
                names_out.append(db.ident(c.name))
                exprs.append(expr)
                params.extend(v)
        filled = {n.strip('"').casefold() for n in names_out}
        empty = [c.name for c in cols.values() if not c.nullable and not c.default and c.name.casefold() not in filled]
        if empty:
            raise ConfigError(f"{e.label}: export columns NOT NULL without a value from Exp_Sql_Txt: {', '.join(empty)}")
        kept = [k for k in keep if k != b["btch_id"]]
        with self.conn.transaction():
            deleted = self.conn.execute_qmark(
                f"""DELETE FROM {export} WHERE {by_src} AND {db.ident(exp_batch)} IN (
                      SELECT Btch_ID FROM ComplianceRequestControl WHERE Project_Cd=? AND Table_Nm=? AND Src_ID=?
                         AND Run_Ty=? AND Rpt_Start_Dt_Key=? AND Rpt_End_Dt_Key=?)"""
                + (f" AND {db.ident(exp_batch)} NOT IN ({', '.join('?' for _ in kept)})" if kept else ""),
                [src_val, *period, *kept]).rowcount
            inserted = 0
            if source is not None:
                inserted = self.conn.execute_qmark(
                    f"INSERT INTO {export} ({', '.join(names_out)}) SELECT {', '.join(exprs)} FROM ({query}) AS q",
                    [*params, *values]).rowcount
            self._done(b, f"exported by Exp_Sql_Txt (data of {source or 'no batch'}): inserted={inserted} deleted={deleted}")
        item.update(status="EXPORTED", export_batch=b["btch_id"], data_from=source or "-", rows_exported=inserted,
                    rows_removed=deleted)
        log.info("EXPORT_LOAD | %s | %s row(s) exported to %s by Exp_Sql_Txt, %s earlier row(s) of the period removed",
                 b["btch_id"], inserted, item["export_table"], deleted)
        return item

    def _done(self, b: dict, detail: str) -> None:
        with self.conn.transaction():
            self.logger.batch_event("EXPORT_LOADED", req_id=b["req_id"], btch_id=b["btch_id"], detail=detail)


# rules: Data quality rules of each exported batch (the table's rule group), run after the export; they never block or
# undo a load or an export.

PENDING_RULES_STATS = ("NOT_RUN", "ERROR")
CORE_LOADS = ("FILE_LOADED", "LATE_ARRIVAL_LOADED", "CORRECTION_LOADED")


@dataclass
class RulesSummary:
    """Result of one FILE_RULES run: batch IDs by outcome."""
    evaluated: int = 0
    passed: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    waiting_for_export: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class RulesRunner:
    """Runs the rules of every batch whose loaded data has not had them yet, one batch at a time: the rules of the
    batch's table (rule group = table, variant = run type, unless ComplianceRuleBinding names others), filtered to
    the batch. A table with an export table waits for the batch's export."""

    def __init__(self, conn, clock: Clock, settings: Settings, rule_engine: RuleEngine):
        self.conn, self.clock, self.settings, self.rules = conn, clock, settings, rule_engine
        self.logger = EventLogger(conn, clock)
        self._exports: dict = {}

    def run(self, scope: Optional[Scope] = None) -> RulesSummary:
        """The batches of a scope (a project, a table group or table, a run type), or of every project."""
        s = RulesSummary()
        where, params = (scope or Scope()).sql("c")
        rows = self.conn.execute(
            "SELECT f.Load_ID, f.Req_ID, f.Btch_ID, f.Stg_Rcd_Cnt, c.Project_Cd, c.Table_Nm FROM ComplianceFileLoad f "
            "JOIN ComplianceRequestControl c ON c.Req_ID = f.Req_ID "
            "WHERE f.Load_Stat = 'FILE_LOADED' AND f.Rules_Stat IN ('NOT_RUN', 'ERROR')" + where + " ORDER BY f.Load_ID",
            params).fetchall()
        self._exports = {(e.project_cd, e.table_nm): e for e in cfgmod.active_export_configs(self.conn, self.settings)}
        waiting = self._not_exported([r["load_id"] for r in rows
                                      if (r["project_cd"], r["table_nm"]) in self._exports])
        batches: dict[str, list[dict]] = {}
        for r in rows:
            batches.setdefault(r["btch_id"], []).append(r)
        for btch_id, loads in batches.items():
            if any(r["load_id"] in waiting for r in loads):
                s.waiting_for_export.append(btch_id)
                continue
            key = f"RULES:{btch_id}"
            if not db.try_lock(self.conn, key):
                s.deferred.append(btch_id)
                continue
            try:
                self._run_batch(btch_id, [r["load_id"] for r in loads], s)
            except Exception as e:  # noqa: BLE001
                log.exception("rules for batch %s failed", btch_id)
                s.errors.append(f"{btch_id}: {db.error_text(e)}")
            finally:
                db.unlock(self.conn, key)
        return s

    def _not_exported(self, load_ids: list[int]) -> set:
        """Loads of tables with an export table whose batch has not been exported since the load (EXPORT_LOAD)."""
        out = set()
        marks, core_loads = db.in_list(CORE_LOADS)
        for i in range(0, len(load_ids), 500):
            ids, chunk = db.in_list(load_ids[i:i + 500])
            out.update(r["load_id"] for r in self.conn.execute(
                f"""SELECT f.Load_ID FROM ComplianceFileLoad f
                      LEFT JOIN (SELECT Load_ID, MAX(Event_Dtts) AS loaded_at FROM ComplianceRequestFileDetail
                                  WHERE Event_Ty IN ({marks}) AND Load_ID IN ({ids}) GROUP BY Load_ID) p
                             ON p.Load_ID = f.Load_ID
                      LEFT JOIN (SELECT Req_ID, MAX(Event_Dtts) AS exported_at FROM ComplianceRequestFileDetail
                                  WHERE Event_Ty = 'EXPORT_LOADED' GROUP BY Req_ID) e ON e.Req_ID = f.Req_ID
                     WHERE f.Load_ID IN ({ids}) AND (e.exported_at IS NULL OR e.exported_at < p.loaded_at)""",
                [*core_loads, *chunk, *chunk]).fetchall())
        return out

    def _run_batch(self, btch_id: str, load_ids: list[int], s: RulesSummary) -> None:
        ids, chunk = db.in_list(load_ids)
        loads = self.conn.execute(f"SELECT * FROM ComplianceFileLoad WHERE Load_ID IN ({ids})", chunk).fetchall()
        loads = [l for l in loads if l["load_stat"] == "FILE_LOADED" and l["rules_stat"] in PENDING_RULES_STATS]
        if not loads:
            return
        s.evaluated += 1
        b = get_batch(self.conn, loads[0]["req_id"])
        ctx = dict(project_cd=b["project_cd"], table_nm=b["table_nm"], run_ty=b["run_ty"], btch_id=btch_id)
        if not any(l["stg_rcd_cnt"] for l in loads):
            self._set(loads, "SKIPPED")
            log.info("FILE_RULES | %s | no data rows: rules skipped", btch_id)
            s.skipped.append(btch_id)
            return
        bindings = cfgmod.rule_bindings(self.conn, b["project_cd"], b["table_nm"], "*", b["run_ty"]) or [
            cfgmod.RuleBinding(b["project_cd"], b["table_nm"], "*", b["run_ty"], b["table_nm"], b["run_ty"])]
        exp = self._exports.get((b["project_cd"], b["table_nm"]))
        log.info("FILE_RULES | %s | rule group(s) %s", btch_id,
                 ", ".join(f"{x.gre_rule_group}/{x.gre_rule_variant}" for x in bindings))
        outcome = self.rules.run(self.conn, bindings, {
            "scope": "BATCH_LEVEL", "btch_id": btch_id, "project_cd": b["project_cd"], "table_nm": b["table_nm"],
            "run_ty": b["run_ty"], "rpt_start_dt_key": b["rpt_start_dt_key"], "rpt_end_dt_key": b["rpt_end_dt_key"],
            "req_dt_key": b["req_dt_key"], "exp_schema_nm": exp.exp_schema_nm if exp else None,
            "exp_table_nm": exp.exp_table_nm if exp else None}, self.settings.file_rules_mode)
        with self.conn.transaction():
            if outcome.status == ERROR:
                self._set(loads, ERROR)
                detail = sanitize_db_error(outcome.error or "")
                if all(l["rules_stat"] != ERROR for l in loads):
                    self.logger.audit("RULES_ENGINE_TECHNICAL_FAILURE", description=detail, **ctx)
                s.errors.append(f"{btch_id}: {detail}")
                return
            if outcome.rules_run == 0:
                self._set(loads, "SKIPPED")
                s.skipped.append(btch_id)
                return
            self._set(loads, outcome.status)
            if not outcome.passed:
                detail = "failed rules: " + ", ".join(outcome.failed_rules)
                self._events(loads, "FILE_RULES_FAILED", detail)
                self.logger.audit("RULES_VALIDATION_FAILED", description=detail, **ctx)
                s.failed.append(btch_id)
            else:
                detail = "warnings: " + ", ".join(outcome.warned_rules) if outcome.warned_rules else None
                self._events(loads, "FILE_RULES_PASSED", detail)
                (s.warned if outcome.warned_rules else s.passed).append(btch_id)

    def _events(self, loads: list[dict], event_ty: str, detail: Optional[str]) -> None:
        for l in loads:
            self.logger.batch_event(event_ty, req_id=l["req_id"], btch_id=l["btch_id"], load_id=l["load_id"],
                                    detail=detail)

    def _set(self, loads: list[dict], rules_stat: str) -> None:
        ids, chunk = db.in_list([l["load_id"] for l in loads])
        self.conn.execute(f"UPDATE ComplianceFileLoad SET Rules_Stat=%s, LastUpdt_Dtts=%s, LastUpdt_ID=USER WHERE Load_ID IN ({ids})",
                          [rules_stat, self.clock.now(), *chunk])


# gre_bridge: File rules through the GRE rules engine: GRE_ENTRYPOINT=compliance_framework.export:run_gre.

logger = logging.getLogger(__name__)

PASSING = ("PASS", "WARN")


def _run_rules():
    package_dir = os.environ.get("GRE_PACKAGE_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules_engine")
    if not os.path.isfile(os.path.join(package_dir, "run_rules.py")):
        raise RuntimeError(f"GRE run_rules.py not found in {package_dir}; set GRE_PACKAGE_DIR")
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)
    from run_rules import run_rules

    return run_rules


def _text(value) -> str:
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _configured(env_key: str) -> dict:
    """run_params / text_params / extra_filters of the Variable's gre section."""
    return json.loads(os.environ.get(env_key) or "{}")


def run_gre(conn, rule_group: str, rule_variant: str, run_params: dict) -> list:
    """One GRE rule_group for one batch -> [{"rule_ref", "passed"}]; raises on a technical failure.

    run_key = the batch ID (a rerun of the batch refreshes its results); the rules see only the batch's rows
    (extra_filters: GRE_BATCH_COLUMN = the batch ID); the framework's values are text_params ({btch_id}, {run_ty} ...
    in a rule; run_type / batch_schedule for gre_exceptions); run_params are only the Variable's own (they also
    filter the record count by column).
    """
    meta_db = os.environ.get("GRE_META_DB")
    if not meta_db:
        raise RuntimeError("GRE_META_DB is not set")
    btch_id = run_params["btch_id"]
    variant = None if rule_variant in (None, "", "*") else rule_variant
    text_params = {**{k: _text(v) for k, v in run_params.items() if v is not None}, "run_type": run_params.get("run_ty"),
                   "batch_schedule": btch_id, **_configured("GRE_TEXT_PARAMS")}
    extra_filters = {**_configured("GRE_EXTRA_FILTERS"), (os.environ.get("GRE_BATCH_COLUMN") or "Btch_ID"): btch_id}
    kwargs = dict(project_name=os.environ.get("GRE_PROJECT_NAME") or run_params.get("project_cd") or None,
                  process_name=os.environ.get("GRE_PROCESS_NAME") or run_params.get("project_cd") or None,
                  rule_group=rule_group, rule_variant=variant, run_key=btch_id, run_params=_configured("GRE_RUN_PARAMS"),
                  extra_filters=extra_filters, text_params=text_params,
                  log_level=os.environ.get("GRE_LOG_LEVEL") or "ERROR")
    run_rules = _run_rules()
    logger.info("FILE_RULES | %s | GRE rule_group=%s rule_variant=%s run_key=%s extra_filters=%s", btch_id,
                rule_group, variant, btch_id, extra_filters)
    outcome, exit_code = run_rules(**kwargs)
    summary = (outcome or {}).get("rule_groups", {}).get(rule_group)
    if summary is None:
        raise RuntimeError(f"GRE returned nothing for rule_group {rule_group} (exit_code={exit_code})")
    if summary["status"] == "NO_RULES":
        logger.warning("GRE rule_group %s (variant %s) has no active rules", rule_group, variant)
        return []
    if summary["status"] != "COMPLETED":
        raise RuntimeError(f"GRE rule_group {rule_group} did not complete: status {summary['status']}")
    if summary.get("errored"):
        raise RuntimeError(f"GRE rule_group {rule_group}: {summary['errored']} rule(s) could not run (see the GRE log "
                           "and gre_rule_errors for run_key " + run_params["btch_id"] + ")")
    rows = conn.execute(f"SELECT rule_id, status FROM {ident(meta_db)}.gre_results WHERE run_id = %s",
                        (summary["run_id"],)).fetchall()
    if not rows:
        raise RuntimeError(f"GRE run {summary['run_id']} wrote no gre_results rows")
    errors = [r["rule_id"] for r in rows if r["status"] not in (*PASSING, "FAIL")]
    if errors:
        raise RuntimeError(f"GRE rule_group {rule_group}: rule(s) {errors} ended in error")
    return [{"rule_ref": f"{rule_group}:{r['rule_id']}", "passed": r["status"] in PASSING} for r in rows]
