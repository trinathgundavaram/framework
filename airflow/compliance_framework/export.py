"""EXPORT_LOAD: copy each batch whose data changed from core into its export table (ComplianceExportConfig).

The export keeps the latest batch with data per source, run type and report dates: its rows replace the rows of
the other batches of that period, except the batches it reuses. A carried-forward batch gets a copy of the reused
batch's core rows under its own batch (batch ID, run type, version, request and report dates of the new batch; every other column, the
row ID included, as in core), so the old and the new batch each have their rows. Rows move on Teradata only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from . import config as cfgmod
from . import db
from .audit import EventLogger
from .common import btch_id_version, code
from .layout import BATCH_COLUMN, LoadContext, column_mapping
from .load import _cast, columns
from .scope import Scope

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


def _as_date(value):
    return value.date() if hasattr(value, "hour") else value


class ExportLoader:
    def __init__(self, conn, clock, settings):
        self.conn, self.clock, self.settings = conn, clock, settings
        self.logger = EventLogger(conn, clock)

    def run(self, scope: Optional[Scope] = None, batches: Optional[list[str]] = None) -> ExportSummary:
        """Export the scope's batches whose data changed since their last export, or the batches named."""
        scope = scope or Scope()
        exports = {(e.project_cd, e.table_nm): e for e in cfgmod.active_export_configs(self.conn, self.settings)
                   if scope.covers(e.project_cd, e.table_nm)}
        s = ExportSummary(tables=sorted(f"{e.exp_schema_nm}.{e.exp_table_nm}" for e in exports.values()))
        todo = self._named(exports, scope, batches, s) if batches else self._changed(exports, scope)
        log.info("EXPORT_LOAD | %s | %s batch(es) to export%s", scope.label(), len(todo),
                 ": " + ", ".join(b["btch_id"] for b in todo[:50]) if todo else "")
        for b in todo:
            e = exports[(b["project_cd"], b["table_nm"])]
            try:
                with db.held(self.conn, db.batch_key(b["btch_id"]), self.settings.lock_timeout_seconds), \
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

    def _named(self, exports: dict, scope: Scope, names: list[str], s: ExportSummary) -> list[dict]:
        out = []
        for name in names:
            b = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID = %s", (code(name),)).fetchone()
            if b is None:
                s.errors.append(f"{name}: no such batch")
            elif (b["project_cd"], b["table_nm"]) not in exports or not scope.covers(b["project_cd"], b["table_nm"], b["run_ty"]):
                s.errors.append(f"{name}: its table has no export table in {scope.label()}")
            else:
                out.append(b)
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
                  LEFT JOIN ComplianceRequestControl r ON r.Btch_ID = c.Reuse_Btch_ID
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
            b = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID = %s",
                                  (b["reuse_btch_id"],)).fetchone()
        return []

    def _export(self, e, b: dict) -> dict:
        b = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Req_ID = %s", (b["req_id"],)).fetchone()
        item = {"batch": b["btch_id"], "source": b["src_id"], "run_type": b["run_ty"],
                "report_dates": f"{_as_date(b['rpt_start_dt_key'])} to {_as_date(b['rpt_end_dt_key'])}",
                "batch_status": b["req_stat"], "export_table": f"{e.exp_schema_nm}.{e.exp_table_nm}"}
        period = (b["project_cd"], b["table_nm"], b["src_id"], b["run_ty"], b["rpt_start_dt_key"], b["rpt_end_dt_key"])
        newest = self.conn.execute(
            """SELECT * FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
                  AND Run_Ty=%s AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s AND Resolution_Ty IS NOT NULL
                ORDER BY Req_ID DESC""", period).fetchone()
        keep = self._data_chain(newest) if newest else []
        if newest and newest["btch_id"] != b["btch_id"] and b["btch_id"] not in keep:
            item.update(status="SKIPPED", detail=f"a later batch of the same period has data: {newest['btch_id']}")
            self._done(b, f"not exported: {item['detail']}")
            log.info("EXPORT_LOAD | %s | skipped: %s", b["btch_id"], item["detail"])
            return item
        cfg = cfgmod.target_tables(self.conn, b["project_cd"], b["table_nm"], b["src_id"], self.settings)
        if cfg is None:
            raise LookupError(f"{b['project_cd']}/{b['table_nm']}/{b['src_id']} has no file or query config")
        chain = self._data_chain(b)
        source = chain[-1] if chain else None
        core = db.qualified(cfg.core_schema_nm, cfg.core_table_nm)
        export = db.qualified(e.exp_schema_nm, e.exp_table_nm)
        core_cols = {c.name for c in columns(self.conn, cfg.core_schema_nm, cfg.core_table_nm) if not c.auto}
        exp_cols = [c for c in columns(self.conn, e.exp_schema_nm, e.exp_table_nm) if not c.auto and c.name in core_cols]
        mapping = column_mapping(cfg, cfgmod.target_columns(self.conn))
        ctx = LoadContext(b["btch_id"], 0, b["run_ty"],
                          btch_id_version(b["btch_id"], b["project_cd"], b["table_nm"], b["src_id"], b["run_ty"]),
                          b["src_id"], "", self.clock.now(), b["project_cd"], b["table_nm"], _as_date(b["rpt_start_dt_key"]),
                          _as_date(b["rpt_end_dt_key"]), b["req_id"], self.settings.business_tz)
        exprs, values = [], []
        for c in exp_cols:
            value_cd = "BATCH_ID" if c.name == BATCH_COLUMN else mapping.get(c.name)
            if source != b["btch_id"] and (value_cd == "BATCH_ID" or value_cd in BATCH_VALUES):
                expr, v = _cast(c, ctx.value(value_cd, c.text, c.temporal))
                exprs.append(expr)
                values.extend(v)
            else:
                exprs.append(db.ident(c.name))
        with self.conn.transaction():
            kept = [k for k in keep if k != b["btch_id"]]
            marks, kept = db.in_list(kept) if kept else ("", [])
            deleted = self.conn.execute(
                f"""DELETE FROM {export} WHERE {db.ident(BATCH_COLUMN)} IN (
                      SELECT Btch_ID FROM ComplianceRequestControl WHERE Project_Cd=%s AND Table_Nm=%s AND Src_ID=%s
                         AND Run_Ty=%s AND Rpt_Start_Dt_Key=%s AND Rpt_End_Dt_Key=%s)"""
                + (f" AND {db.ident(BATCH_COLUMN)} NOT IN ({marks})" if kept else ""), [*period, *kept]).rowcount
            inserted = 0
            if source is not None:
                text = (f"INSERT INTO {export} ({', '.join(db.ident(c.name) for c in exp_cols)}) "
                        f"SELECT {', '.join(exprs)} FROM {core} WHERE {db.ident(BATCH_COLUMN)} = ?")
                inserted = self.conn.execute_qmark(text, [*values, source]).rowcount
            how = ("no data" if source is None else "its own rows" if source == b["btch_id"]
                   else f"a copy of {source}")
            self._done(b, f"exported {how}: inserted={inserted} deleted={deleted}")
        item.update(status="EXPORTED", data_from=source or "-", rows_exported=inserted, rows_removed=deleted)
        log.info("EXPORT_LOAD | %s | %s row(s) exported to %s (%s), %s earlier row(s) of the period removed",
                 b["btch_id"], inserted, item["export_table"], how, deleted)
        return item

    def _done(self, b: dict, detail: str) -> None:
        with self.conn.transaction():
            self.logger.batch_event("EXPORT_LOADED", req_id=b["req_id"], btch_id=b["btch_id"], detail=detail)
