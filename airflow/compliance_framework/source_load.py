"""SOURCE_LOAD: pull a batch's data from an internal source with its own SELECT (ComplianceSourceQueryConfig).

Each pull is a load like a file's (a ComplianceFileLoad row, the same staging, core load, statuses, audit and step
report). A source on the framework's own Teradata system is copied with INSERT ... SELECT; a source on another
server (SQL Server, another Teradata) is read in batches through its own connection and inserted.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Optional

from . import config as cfgmod
from . import db
from .audit import EventLogger
from .batches import get_batch
from .common import FileRejected, btch_id_version, code
from .config import QueryConfig
from .connection_factory import source_connection
from .control_report import QUERY_PREFIX, file_details
from .filecheck_options import check_options
from .ingest import IngestOutcome, PathIngestSummary
from .layout import PLACEHOLDER, LoadContext, build_layout, column_mapping, sql_body
from .load import columns, sanitize_db_error, stage_query, stage_rows
from .scope import Scope

log = logging.getLogger(__name__)


def _as_date(value):
    return value.date() if hasattr(value, "hour") else value


class SourceLoader:
    def __init__(self, conn, clock, settings, pipeline):
        self.conn, self.clock, self.settings, self.pipeline = conn, clock, settings, pipeline
        self.logger = EventLogger(conn, clock)
        self._sources: dict = {}

    def run(self, scope: Optional[Scope] = None, batches: Optional[list[str]] = None) -> PathIngestSummary:
        """Pull the batches of the scope's query sources that have no data yet, or the batches named in `batches`
        (whatever their data: a new pull replaces the batch's rows)."""
        scope = scope or Scope()
        cfgs = {(q.project_cd, q.table_nm, q.src_id): q for q in cfgmod.active_query_configs(self.conn, self.settings)
                if scope.covers(q.project_cd, q.table_nm)}
        summary = PathIngestSummary(locations=sorted({f"{q.db_ty} {q.conn_cd or 'same Teradata system'}"
                                                      for q in cfgs.values()}))
        todo = self._batches(cfgs, scope, batches, summary)
        summary.scanned = len(todo)
        log.info("SOURCE_LOAD | %s | %s batch(es) to pull%s", scope.label(), len(todo),
                 ": " + ", ".join(b["btch_id"] for b, _ in todo[:50]) if todo else "")
        handled = []
        try:
            for batch, q in todo:
                load_id = None
                try:
                    load_id = self._register(batch, q)
                    handled.append(load_id)
                    summary.tally(self._pull(load_id, batch, q))
                except Exception as e:  # noqa: BLE001
                    summary.errors.append(f"{batch['btch_id']}: {db.error_text(e)}")
                    if load_id is not None:
                        self.pipeline._mark_technical_failure(load_id, e)
        finally:
            for c in self._sources.values():
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    log.debug("closing a source connection", exc_info=True)
            self._sources = {}
            try:
                summary.files = file_details(self.conn, handled, self.pipeline._load_config(), self.settings.business_tz)
            except Exception as e:  # noqa: BLE001
                log.warning("could not read what happened to the pulls: %s", db.error_text(e))
        return summary

    def _batches(self, cfgs: dict, scope: Scope, names: Optional[list[str]], summary) -> list[tuple[dict, QueryConfig]]:
        if names:
            out = []
            for name in names:
                b = self.conn.execute("SELECT * FROM ComplianceRequestControl WHERE Btch_ID = %s", (code(name),)).fetchone()
                q = cfgs.get((b["project_cd"], b["table_nm"], b["src_id"])) if b else None
                if b is None:
                    summary.errors.append(f"{name}: no such batch")
                elif q is None or not scope.covers(b["project_cd"], b["table_nm"], b["run_ty"]):
                    summary.errors.append(f"{name}: not a batch of a query source in {scope.label()}")
                else:
                    out.append((b, q))
            return out
        if not cfgs:
            return []
        where, params = scope.sql("c")
        rows = self.conn.execute(
            f"""SELECT c.* FROM ComplianceRequestControl c
                 WHERE c.Batch_Close_Ind = 0 AND c.Req_Stat = 'REQUEST_CREATED' AND c.Req_Dt_Key <= %s{where}
                 ORDER BY c.Req_ID""", [self.clock.today(self.settings.business_tz), *params]).fetchall()
        return [(b, cfgs[(b["project_cd"], b["table_nm"], b["src_id"])]) for b in rows
                if (b["project_cd"], b["table_nm"], b["src_id"]) in cfgs]

    def _register(self, batch: dict, q: QueryConfig) -> int:
        """The load row of one pull; its 'file' is the source and the batch."""
        now = self.clock.now()
        share = f"{QUERY_PREFIX}{q.conn_cd or 'LOCAL'}"
        path = f"{q.project_cd}/{q.table_nm}/{q.src_id}/{batch['btch_id']}"
        key = db.file_hash(share, path, f"{now.isoformat()}#{uuid.uuid4().hex}")
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO ComplianceFileLoad (File_Hash, File_Share, File_Path, File_Version, File_Size_Byte,
                                                  Load_Stat, Rules_Stat, Created_Dtts, Updated_Dtts)
                   VALUES (%s,%s,%s,%s,0,'RECEIVED','NOT_RUN',%s,%s)""", (key, share, path, now.isoformat(), now, now))
            return self.conn.execute("SELECT Load_ID FROM ComplianceFileLoad WHERE File_Hash=%s", (key,)).fetchone()["load_id"]

    def _layout_for(self, q: QueryConfig, opts, names: list[str]):
        return build_layout(q.label, columns(self.conn, q.stg_schema_nm, q.stg_table_nm),
                            columns(self.conn, q.core_schema_nm, q.core_table_nm),
                            column_mapping(q, cfgmod.target_columns(self.conn)),
                            f"{q.stg_schema_nm}.{q.stg_table_nm}", f"{q.core_schema_nm}.{q.core_table_nm}",
                            () if opts.target_columns else tuple(n.lower() for n in names), None, opts.target_columns)

    def _pull(self, load_id: int, batch: dict, q: QueryConfig) -> IngestOutcome:
        ref = f"{q.db_ty} {q.conn_cd or 'same Teradata system'}: {q.project_cd}/{q.table_nm}/{q.src_id} {batch['btch_id']}"
        with self.conn.transaction():
            self.conn.execute("UPDATE ComplianceFileLoad SET Cfg_ID=NULL, Req_ID=%s, Btch_ID=%s, Load_Stat='STAGING', "
                              "Updated_Dtts=%s WHERE Load_ID=%s", (batch["req_id"], batch["btch_id"], self.clock.now(), load_id))
        opts = check_options(q, cfgmod.file_checks(self.conn))
        stage_lock = db.batch_key(f"STAGE {q.stg_schema_nm}.{q.stg_table_nm}".upper())
        with db.held(self.conn, db.batch_key(batch["btch_id"]), self.settings.lock_timeout_seconds), \
                db.held(self.conn, stage_lock, self.settings.lock_timeout_seconds):
            b = get_batch(self.conn, batch["req_id"])
            ctx = LoadContext(b["btch_id"], load_id, b["run_ty"],
                              btch_id_version(b["btch_id"], q.project_cd, q.table_nm, q.src_id, b["run_ty"]), q.src_id,
                              f"{q.project_cd}/{q.table_nm}/{q.src_id}", self.clock.now(), q.project_cd, q.table_nm,
                              _as_date(b["rpt_start_dt_key"]), _as_date(b["rpt_end_dt_key"]), b["req_id"],
                              self.settings.business_tz)
            try:
                if q.same_system:
                    rows, layout = stage_query(self.conn, cfg=q, layout_for=lambda names: self._layout_for(q, opts, names),
                                               ctx=ctx, sql=q.sql)
                else:
                    rows, layout = self._stage_from_source(q, opts, ctx)
            except FileRejected as e:
                return self._reject(load_id, b, q, e.event_ty, str(e), ref)
            log.info("SOURCE_LOAD | %s | load %s | %s row(s) %s into %s.%s", b["btch_id"], load_id, rows,
                     "copied on Teradata" if q.same_system else f"read from {q.conn_cd}", q.stg_schema_nm, q.stg_table_nm)
            with self.conn.transaction():
                self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='STAGED', Stg_Rcd_Cnt=%s, Updated_Dtts=%s "
                                  "WHERE Load_ID=%s", (rows, self.clock.now(), load_id))
            self.pipeline._load = (layout, ctx)
            return self.pipeline._resolve(load_id, None, q, b["req_id"], rows, ref=ref)

    def _source(self, q: QueryConfig):
        if q.conn_cd not in self._sources:
            self._sources[q.conn_cd] = source_connection(q.conn_cd, q.db_ty)
        return self._sources[q.conn_cd]

    def _stage_from_source(self, q: QueryConfig, opts, ctx: LoadContext):
        body = sql_body(f"{q.label}: Src_Sql_Txt", q.sql)
        params = ctx.sql_params()
        values = [_plain(params[n.lower()]) for n in PLACEHOLDER.findall(body)]
        try:
            cur = self._source(q).cursor()
            cur.execute(PLACEHOLDER.sub("?", body), values)
        except Exception as e:  # noqa: BLE001
            raise FileRejected("SOURCE_QUERY_FAILED", f"the query on {q.conn_cd} failed: {sanitize_db_error(db.error_line(e))}")
        try:
            names = [d[0] for d in cur.description]
            layout = self._layout_for(q, opts, names)

            def rows():
                while True:
                    chunk = cur.fetchmany(self.settings.insert_batch_rows)
                    if not chunk:
                        return
                    yield from (list(r) for r in chunk)
            return stage_rows(self.conn, self.settings, cfg=q, layout=layout, ctx=ctx, records=rows()), layout
        finally:
            cur.close()

    def _reject(self, load_id: int, b: dict, q: QueryConfig, event_ty: str, message: str, ref: str) -> IngestOutcome:
        with self.conn.transaction():
            self.conn.execute("UPDATE ComplianceFileLoad SET Load_Stat='QUARANTINED', Quarantine_Rsn_Cd=%s, Error_Txt=%s, "
                              "Updated_Dtts=%s WHERE Load_ID=%s", (event_ty, message[:4000], self.clock.now(), load_id))
            self.logger.audit(event_ty, load_id=load_id, file_ref=ref[:1024], description=message, project_cd=q.project_cd,
                              table_nm=q.table_nm, src_id=q.src_id, run_ty=b["run_ty"], req_id=b["req_id"],
                              btch_id=b["btch_id"])
        return IngestOutcome(load_id, "QUARANTINED", event_ty=event_ty, message=message, req_id=b["req_id"])


def _plain(value):
    """A parameter value a source driver accepts (dates stay dates)."""
    return value if value is None or isinstance(value, (str, int, float, date)) else str(value)
