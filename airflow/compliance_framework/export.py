"""EXPORT_LOAD, FILE_RULES and the bridge to the GRE rules engine."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from . import config as cfgmod, db
from .adapters import ERROR, RuleEngine
from .audit import EventLogger
from .batches import Releases, get_batch
from .common import Clock, ConfigError, code
from .config import Scope
from .db import Settings, ident
from .load import BATCH_COLUMN, LoadContext, _cast, batch_column, column_mapping, columns, sanitize_db_error


# export: EXPORT_LOAD: copy each batch whose data changed from core into its export table (ComplianceExportConfig).

log = logging.getLogger(__name__)

CHANGES = ("FILE_PROMOTED", "LATE_ARRIVAL_PROMOTED", "CORRECTION_PROMOTED", "CARRY_FORWARD_APPLIED",
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
        for b in todo:
            e = exports[(b["project_cd"], b["table_nm"])]
            try:
                with db.held(self.conn, db.req_key(b), self.settings.lock_timeout_seconds), \
                        db.held(self.conn, db.batch_key(f"EXPORT {e.exp_schema_nm}.{e.exp_table_nm}".upper()),
                                self.settings.lock_timeout_seconds):
                    item = self._export(e, b)
            except Exception as err:  # noqa: BLE001
                s.failed += 1
                s.errors.append(f"{b['btch_id']}: {db.error_text(err)}")
                item = {"batch": b["btch_id"], "status": "FAILED", "detail": db.error_text(err)}
                with self.conn.transaction():
                    self.logger.audit("EXPORT_LOAD_FAILED", description=db.error_text(err), project_cd=b["project_cd"],
                                      table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"],
                                      req_id=b["req_id"], btch_id=b["btch_id"])
            s.batches.append(item)
            if item["status"] == "EXPORTED":
                s.exported += 1
            elif item["status"] == "SKIPPED":
                s.skipped += 1
        return s

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
                     and scope.covers(b["project_cd"], b["table_nm"], b["run_ty"])]
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
        core = db.qualified(cfg.core_schema_nm, cfg.core_table_nm)
        core_cols = {c.name for c in columns(self.conn, cfg.core_schema_nm, cfg.core_table_nm) if not c.auto}
        core_batch = batch_column(core_cols, mapping)
        exp_cols = [c for c in exp_all if not c.auto and (c.name in core_cols or c.name in (e.exp_btch_col, exp_batch))]
        exprs, values = [], []
        for c in exp_cols:
            value_cd = "BATCH_ID" if c.name in (BATCH_COLUMN, exp_batch) else mapping.get(c.name)
            if c.name == e.exp_btch_col:
                expr, v = _cast(c, exp_btch_id)
                exprs.append(expr)
                values.extend(v)
            elif source != b["btch_id"] and (value_cd == "BATCH_ID" or value_cd in BATCH_VALUES):
                expr, v = _cast(c, ctx.value(value_cd, c.text, c.temporal))
                exprs.append(expr)
                values.extend(v)
            else:
                exprs.append(db.ident(core_batch if c.name == exp_batch else c.name))
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
                        f"SELECT {', '.join(exprs)} FROM {core} WHERE {db.ident(core_batch)} = ? AND {by_src}")
                inserted = self.conn.execute_qmark(text, [*values, source, src_val]).rowcount
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

    def _done(self, b: dict, detail: str) -> None:
        with self.conn.transaction():
            self.logger.batch_event("EXPORT_LOADED", req_id=b["req_id"], btch_id=b["btch_id"], detail=detail)


# rules: File rules, run after the core load (design §9.4); they never block or undo a load.

PENDING_RULES_STATS = ("NOT_RUN", "ERROR")
PROMOTIONS = ("FILE_PROMOTED", "LATE_ARRIVAL_PROMOTED", "CORRECTION_PROMOTED")


@dataclass
class RulesSummary:
    """Result of one FILE_RULES run: Load_IDs by outcome."""
    evaluated: int = 0
    passed: list[int] = field(default_factory=list)
    warned: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    deferred: list[int] = field(default_factory=list)
    waiting_for_export: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class RulesRunner:
    """Runs the bound rules for every loaded file that has not had them yet."""

    def __init__(self, conn, clock: Clock, settings: Settings, rule_engine: RuleEngine):
        self.conn, self.clock, self.settings, self.rules = conn, clock, settings, rule_engine
        self.logger = EventLogger(conn, clock)
        self._exports: dict = {}

    def run(self, scope: Optional[Scope] = None) -> RulesSummary:
        """The loaded files of a scope (a project, a table group or table, a run type), or of every project."""
        s = RulesSummary()
        where, params = (scope or Scope()).sql("c")
        rows = self.conn.execute(
            "SELECT f.Load_ID, f.Req_ID, f.Btch_ID, c.Project_Cd, c.Table_Nm FROM ComplianceFileLoad f "
            "JOIN ComplianceRequestControl c ON c.Req_ID = f.Req_ID "
            "WHERE f.Load_Stat = 'PROMOTED' AND f.Rules_Stat IN ('NOT_RUN', 'ERROR')" + where + " ORDER BY f.Load_ID",
            params).fetchall()
        self._exports = {(e.project_cd, e.table_nm): e for e in cfgmod.active_export_configs(self.conn, self.settings)}
        waiting = self._not_exported([r["load_id"] for r in rows
                                      if (r["project_cd"], r["table_nm"]) in self._exports])
        for r in rows:
            if r["load_id"] in waiting:
                s.waiting_for_export.append(r["load_id"])
                continue
            key = db.req_key(r)
            if not db.try_lock(self.conn, key):
                s.deferred.append(r["load_id"])
                continue
            try:
                self._run_load(r["load_id"], s)
            except Exception as e:  # noqa: BLE001
                log.exception("rules for load %s failed", r["load_id"])
                s.errors.append(f"load {r['load_id']}: {db.error_text(e)}")
            finally:
                db.unlock(self.conn, key)
        return s

    def _not_exported(self, load_ids: list[int]) -> set:
        """Loads of tables with an export table whose batch has not been exported since the load (EXPORT_LOAD)."""
        out = set()
        marks, promotions = db.in_list(PROMOTIONS)
        for i in range(0, len(load_ids), 500):
            ids, chunk = db.in_list(load_ids[i:i + 500])
            out.update(r["load_id"] for r in self.conn.execute(
                f"""SELECT f.Load_ID FROM ComplianceFileLoad f
                      LEFT JOIN (SELECT Load_ID, MAX(Event_Dtts) AS promoted_at FROM ComplianceRequestFileDetail
                                  WHERE Event_Ty IN ({marks}) AND Load_ID IN ({ids}) GROUP BY Load_ID) p
                             ON p.Load_ID = f.Load_ID
                      LEFT JOIN (SELECT Req_ID, MAX(Event_Dtts) AS exported_at FROM ComplianceRequestFileDetail
                                  WHERE Event_Ty = 'EXPORT_LOADED' GROUP BY Req_ID) e ON e.Req_ID = f.Req_ID
                     WHERE f.Load_ID IN ({ids}) AND (e.exported_at IS NULL OR e.exported_at < p.promoted_at)""",
                [*promotions, *chunk, *chunk]).fetchall())
        return out

    def _run_load(self, load_id: int, s: RulesSummary) -> None:
        load = self.conn.execute("SELECT * FROM ComplianceFileLoad WHERE Load_ID=%s", (load_id,)).fetchone()
        if load["load_stat"] != "PROMOTED" or load["rules_stat"] not in PENDING_RULES_STATS:
            return
        s.evaluated += 1
        b = get_batch(self.conn, load["req_id"])
        ctx = dict(project_cd=b["project_cd"], table_nm=b["table_nm"], src_id=b["src_id"], run_ty=b["run_ty"],
                   req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id)
        bindings = cfgmod.rule_bindings(self.conn, b["project_cd"], b["table_nm"], b["src_id"], b["run_ty"])
        cfg = cfgmod.target_tables(self.conn, b["project_cd"], b["table_nm"], b["src_id"], settings=self.settings)
        if not bindings or cfg is None:
            self._set(load_id, "SKIPPED")
            s.skipped.append(load_id)
            return
        exp = self._exports.get((b["project_cd"], b["table_nm"]))
        outcome = self.rules.run(self.conn, bindings, {
            "scope": "FILE_LEVEL", "btch_id": b["btch_id"], "load_id": load_id,
            "project_cd": b["project_cd"], "table_nm": b["table_nm"], "src_id": b["src_id"], "run_ty": b["run_ty"],
            "rpt_start_dt_key": b["rpt_start_dt_key"], "rpt_end_dt_key": b["rpt_end_dt_key"],
            "req_dt_key": b["req_dt_key"], "stg_schema_nm": cfg.stg_schema_nm, "stg_table_nm": cfg.stg_table_nm,
            "core_schema_nm": cfg.core_schema_nm, "core_table_nm": cfg.core_table_nm,
            "exp_schema_nm": exp.exp_schema_nm if exp else None, "exp_table_nm": exp.exp_table_nm if exp else None,
            "exp_btch_id": b["btch_id"] if exp else None},
            self.settings.file_rules_mode)
        with self.conn.transaction():
            self._set(load_id, outcome.status)
            if outcome.status == ERROR:
                detail = sanitize_db_error(outcome.error or "")
                if load["rules_stat"] != ERROR:
                    self.logger.audit("RULES_ENGINE_TECHNICAL_FAILURE", description=detail, **ctx)
                s.errors.append(f"load {load_id}: {detail}")
            elif not outcome.passed:
                detail = "failed rules: " + ", ".join(outcome.failed_rules)
                self.logger.batch_event("FILE_RULES_FAILED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                self.logger.audit("RULES_VALIDATION_FAILED", description=detail, **ctx)
                s.failed.append(load_id)
            else:
                detail = "warnings: " + ", ".join(outcome.warned_rules) if outcome.warned_rules else None
                self.logger.batch_event("FILE_RULES_PASSED", req_id=b["req_id"], btch_id=b["btch_id"], load_id=load_id,
                                        detail=detail)
                (s.warned if outcome.warned_rules else s.passed).append(load_id)

    def _set(self, load_id: int, rules_stat: str) -> None:
        self.conn.execute("UPDATE ComplianceFileLoad SET Rules_Stat=%s, Updated_Dtts=%s WHERE Load_ID=%s",
                          (rules_stat, self.clock.now(), load_id))


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
    """One GRE rule_group for one staged file -> [{"rule_ref", "passed"}]; raises on a technical failure."""
    meta_db = os.environ.get("GRE_META_DB")
    if not meta_db:
        raise RuntimeError("GRE_META_DB is not set")
    run_key = f"CBF_LOAD_{run_params['load_id']}"
    params = {**_configured("GRE_RUN_PARAMS"), **{k: _text(v) for k, v in run_params.items() if v is not None}}
    variant = None if rule_variant in (None, "", "*") else rule_variant
    outcome, exit_code = _run_rules()(project_name=os.environ.get("GRE_PROJECT_NAME") or None, rule_group=rule_group,
                                      rule_variant=variant, run_key=run_key, run_params=params,
                                      extra_filters=_configured("GRE_EXTRA_FILTERS"),
                                      text_params=_configured("GRE_TEXT_PARAMS"),
                                      log_level=os.environ.get("GRE_LOG_LEVEL") or "ERROR")
    summary = (outcome or {}).get("rule_groups", {}).get(rule_group)
    if summary is None:
        raise RuntimeError(f"GRE returned nothing for rule_group {rule_group} (exit_code={exit_code})")
    if summary["status"] == "NO_RULES":
        logger.warning("GRE rule_group %s (variant %s) has no active rules", rule_group, variant)
        return []
    if summary["status"] != "COMPLETED" or summary.get("errored"):
        raise RuntimeError(f"GRE rule_group {rule_group} did not complete: status={summary['status']} "
                           f"errored={summary.get('errored')}")
    rows = conn.execute(f"SELECT rule_id, status FROM {ident(meta_db)}.gre_results WHERE run_id = %s",
                        (summary["run_id"],)).fetchall()
    if not rows:
        raise RuntimeError(f"GRE run {summary['run_id']} wrote no gre_results rows")
    errors = [r["rule_id"] for r in rows if r["status"] not in (*PASSING, "FAIL")]
    if errors:
        raise RuntimeError(f"GRE rule_group {rule_group}: rule(s) {errors} ended in error")
    return [{"rule_ref": f"{rule_group}:{r['rule_id']}", "passed": r["status"] in PASSING} for r in rows]
