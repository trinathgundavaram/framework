"""The application object and the steps (modules) it runs."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Mapping, Optional

from compliance_framework import config as cfgmod
from compliance_framework.adapters import FileStore, RuleEngine, build_channel, build_file_store, build_rule_engine, dirname
from compliance_framework.audit import NotificationDispatcher
from compliance_framework.batches import (BatchCloser, DecisionProcessor, IntakeProcessor, ScheduleSummary, create_batches,
    run_type_list, wanted)
from compliance_framework.common import ADHOC, Clock, ConfigError, SCHEDULED, code
from compliance_framework.config import resolve_scope
from compliance_framework.db import Connection, Settings, held_locks, schema_exists
from compliance_framework.export import RulesRunner
from compliance_framework.filecheck import FileChecker
from compliance_framework.ingest import IngestPipeline
from compliance_framework.load import MESSAGES


# app: Service wiring (design §15.3 for the operational health report).

@dataclass
class App:
    conn: Connection
    clock: Clock
    settings: Settings
    store: FileStore
    rules: RuleEngine

    @classmethod
    def from_settings(cls, settings: Settings, clock: Optional[Clock] = None) -> "App":
        conn = settings.connect()
        try:
            if not schema_exists(conn, settings.metadata_schema):
                raise ConfigError(f"the framework tables were not found in {settings.metadata_schema!r}; "
                                  "they are created separately from airflow_resources/sql/schema.sql")
            return cls(conn, clock or Clock(), settings, build_file_store(settings), build_rule_engine(settings))
        except BaseException:
            conn.close()
            raise

    def close(self) -> None:
        if not self.conn.closed:
            self.conn.close()

    def __enter__(self) -> "App":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @cached_property
    def closer(self) -> BatchCloser:
        return BatchCloser(self.conn, self.clock, self.settings)

    @cached_property
    def pipeline(self) -> IngestPipeline:
        return IngestPipeline(self.conn, self.clock, self.settings, self.store)

    @cached_property
    def file_checker(self) -> FileChecker:
        return FileChecker(self.conn, self.clock, self.settings, self.store, self.pipeline)

    @cached_property
    def source_loader(self):
        from compliance_framework.ingest import SourceLoader
        return SourceLoader(self.conn, self.clock, self.settings, self.pipeline)

    @cached_property
    def exporter(self):
        from compliance_framework.export import ExportLoader
        return ExportLoader(self.conn, self.clock, self.settings)

    @cached_property
    def rules_runner(self) -> RulesRunner:
        return RulesRunner(self.conn, self.clock, self.settings, self.rules)

    @cached_property
    def decisions(self) -> DecisionProcessor:
        return DecisionProcessor(self.conn, self.clock, self.settings)

    @cached_property
    def intake(self) -> IntakeProcessor:
        return IntakeProcessor(self.conn, self.clock, self.settings)

    def run_module(self, name: str, params: Optional[dict] = None) -> ModuleOutcome:
        """Run a module by name (see modules.py)."""
        return run_module(self, name, params)

    def create_batches(self, **kw) -> ScheduleSummary:
        return create_batches(self.conn, self.clock, self.settings, **kw)

    def notifier(self, channel=None) -> NotificationDispatcher:
        return NotificationDispatcher(self.conn, self.settings, channel or build_channel(self.settings))

    def health(self) -> dict[str, list[dict]]:
        """Operational report (design §15.3), composed from the service that owns each set of tables."""
        return {**self.pipeline.health(), **self.decisions.health(), **self.closer.health(),
                "locks_held": held_locks(self.conn)}


# modules: Module dispatcher.

@dataclass
class ModuleOutcome:
    module: str
    result: Any
    exit_code: int


@dataclass(frozen=True)
class Param:
    name: str
    kind: type = str


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    description: str
    required: tuple[Param, ...]
    optional: tuple[Param, ...]
    handler: Callable[[Any, dict], ModuleOutcome]

    @property
    def params(self) -> dict[str, Param]:
        return {p.name: p for p in (*self.required, *self.optional)}


CODE_PARAMS = ("project", "run_type", "table", "table_group")
SCOPE = (Param("table_group"), Param("table"), Param("run_type"))


def _scope(app, p: dict):
    return resolve_scope(app.conn, p.get("project"), p.get("table_group"), p.get("table"), p.get("run_type"))


def normalize(name: str) -> str:
    return (name or "").strip().upper().replace("-", "_").replace(" ", "_")


def _key(name: str) -> str:
    return name.strip().lstrip("-").lower().replace("-", "_")


@dataclass
class BatchCreationSummary:
    """One BATCH_CREATION call's work for one project."""
    scheduled: Optional[Any] = None
    adhoc: Optional[Any] = None


def _batch_creation(app, p: dict) -> ModuleOutcome:
    """Scheduled batches that are due by their configuration, then the intake requests (of `run_type` when given)."""
    project, run_type = p["project"], p.get("run_type")
    scope = _scope(app, p)
    period_args = ("period", "period_file", "lookback_days", "lookback_weeks")
    scheduled_kw = dict(project_cd=project, table_nm=p.get("table"), run_ty=run_type, period=p.get("period"),
                        period_file=p.get("period_file"), lookback_days=p.get("lookback_days"),
                        lookback_weeks=p.get("lookback_weeks"), tables=scope.tables if p.get("table_group") else None)
    scheduled = None
    if run_type:
        rt = cfgmod.run_type(app.conn, run_type)
        if rt is None or not rt.active:
            raise ConfigError(f"run type {run_type} is unknown or inactive")
        if rt.run_category_cd == SCHEDULED or rt.has_schedule:
            scheduled = app.create_batches(**scheduled_kw)
        elif any(k in p for k in period_args):
            raise ConfigError(f"run type {run_type} is {ADHOC} without a schedule; --period/--period-file/"
                              f"--lookback-* only apply to a run type with a schedule")
    else:
        scheduled = app.create_batches(**scheduled_kw)
    adhoc = app.intake.run(project_cd=project, run_ty=run_type, tables=scope.tables)
    errors = bool(scheduled and scheduled.errors) or bool(adhoc.failed)
    return ModuleOutcome("BATCH_CREATION", BatchCreationSummary(scheduled, adhoc), 1 if errors else 0)


def _summary(files: list) -> dict:
    count = {s: sum(f["status"] == s for f in files) for s in ("ACCEPTED", "REJECTED", "FAILED")}
    return {k.lower(): v for k, v in count.items()}


def report(out: ModuleOutcome) -> dict:
    """The run as it is shown in the log: counts first, then one block per file (FILE_LOAD / FILE_CHECK)."""
    r = out.result
    if out.module == "FILE_LOAD":
        counts = _summary(r.files)
        not_loaded = counts["rejected"] + counts["failed"]
        return {"step": "FILE_LOAD", "result": ("NO FILES" if not r.files and not r.errors
                                                 else f"{not_loaded} FILE(S) NOT LOADED" if not_loaded or r.errors
                                                 else "ALL FILES LOADED"),
                "folders": r.locations, "files_found": r.scanned, **counts, "skipped": r.skipped + r.other,
                "control_reports": r.reports,
                "files": [{k: v for k, v in f.items() if k not in ("share", "folder") and v is not None} for f in r.files],
                "problems": r.errors}
    if out.module == "SOURCE_LOAD":
        counts = _summary(r.files)
        not_loaded = counts["rejected"] + counts["failed"]
        deferred = sum(f["status"] == "WAITING" and f.get("reason") == MESSAGES["DEFERRED"] for f in r.files)
        waiting = sum(f["status"] == "WAITING" for f in r.files) - deferred
        return {"step": "SOURCE_LOAD", "result": ("NO BATCHES TO PULL" if not r.files and not r.errors
                                                   else f"{not_loaded or len(r.errors)} BATCH(ES) NOT LOADED"
                                                   if not_loaded or r.errors
                                                   else f"{waiting} BATCH(ES) WITHOUT DATA YET" if waiting
                                                   else f"{deferred} BATCH(ES) LEFT FOR THE NEXT RUN" if deferred
                                                   else "ALL BATCHES LOADED"),
                "sources": r.locations, "batches_found": r.scanned, **counts, **({"waiting": waiting} if waiting else {}),
                **({"deferred": deferred} if deferred else {}),
                **({"released": r.released} if r.released else {}), **({"held": r.held} if r.held else {}),
                "loads": [{("rows_read" if k == "rows_in_file" else k): v for k, v in f.items()
                           if k not in ("file", "share", "folder", "file_date") and v is not None} for f in r.files],
                "problems": r.errors}
    if out.module == "EXPORT_LOAD":
        return {"step": "EXPORT_LOAD", "result": ("NO BATCHES TO EXPORT" if not r.batches and not r.errors
                                                   else f"{r.failed or len(r.errors)} BATCH(ES) NOT EXPORTED"
                                                   if r.failed or r.errors else "ALL BATCHES EXPORTED"),
                "export_tables": r.tables, "batches_found": len(r.batches), "exported": r.exported,
                "skipped": r.skipped, "failed": r.failed, **({"released": r.released} if r.released else {}),
                **({"held": r.held} if r.held else {}), "batches": r.batches, "problems": r.errors}
    if out.module == "FILE_CHECK":
        return {"step": "FILE_CHECK", "result": ("NO FILES" if not r.files and not r.errors
                                                  else f"{r.failed} FILE(S) WITH PROBLEMS" if r.failed or r.errors
                                                  else "ALL FILES PASSED"),
                "folders": r.locations, "files_checked": r.checked, "passed": r.passed, "failed": r.failed,
                "not_checked": r.skipped,
                "files": [{k: v for k, v in {
                    "file": f["file"].rsplit("/", 1)[-1], "result": f["result"], "source": f["config"],
                    "run_type": f["run_type"], "version": f["version"],
                    "report_dates": f"{f['rpt_start']} to {f['rpt_end']}" if f["rpt_start"] else None,
                    "batch": f["batch"], **(f.get("records") or {}), "problems": f["findings"] or None,
                    "notes": f["notes"] or None,
                    "error_log": f.get("error_file"), "moved_to": f.get("moved_to")}.items() if v is not None}
                          for f in r.files],
                "problems": r.errors}
    return {"step": out.module, "result": "OK" if out.exit_code == 0 else "COMPLETED WITH PROBLEMS", "details": r}


def _file_load(app, p: dict) -> ModuleOutcome:
    """Every file is processed; exit 1 when any of them was not loaded (after all were tried)."""
    from compliance_framework.ingest import PathIngestSummary

    share, file, folder, project = p.get("share"), p.get("file"), p.get("folder"), p.get("project")
    if (project or p.get("table_group") or p.get("table") or p.get("run_type")) and (share or file or folder):
        raise ConfigError("FILE_LOAD takes --project (that project's configured folders) or "
                          "--share/--file/--folder, not both")
    if file:
        if not share:
            raise ConfigError("FILE_LOAD with --file also needs --share")
        if folder:
            raise ConfigError("FILE_LOAD takes --file (one file) or --folder (one folder), not both")
        s = PathIngestSummary(locations=["/".join((share, dirname(file)))])
        try:
            s.scanned = 1
            s.tally(app.pipeline.process_file(share, file))
        finally:
            s.reports, s.files = app.pipeline.control_report()
        return ModuleOutcome("FILE_LOAD", s, 1 if s.quarantined or s.rejected else 0)
    s = app.pipeline.process_path(share, folder, _scope(app, p))
    return ModuleOutcome("FILE_LOAD", s, 1 if s.errors or s.quarantined or s.rejected else 0)


def _source_load(app, p: dict) -> ModuleOutcome:
    """Pull internal sources by batch; exit 1 when a batch could not be pulled or loaded (after all were tried)."""
    names = [n.strip() for n in (p.get("batch") or "").split(",") if n.strip()]
    s = app.source_loader.run(_scope(app, p), names or None, release=wanted(p.get("release")),
                                   hold=run_type_list(p.get("hold")))
    return ModuleOutcome("SOURCE_LOAD", s, 1 if s.errors or s.quarantined or s.rejected else 0)


def _export_load(app, p: dict) -> ModuleOutcome:
    """Copy the batches whose data changed into the export tables; exit 1 when a batch could not be exported."""
    names = [n.strip() for n in (p.get("batch") or "").split(",") if n.strip()]
    s = app.exporter.run(_scope(app, p), names or None, release=wanted(p.get("release")),
                                   hold=run_type_list(p.get("hold")))
    return ModuleOutcome("EXPORT_LOAD", s, 1 if s.errors or s.failed else 0)


def _file_check(app, p: dict) -> ModuleOutcome:
    """Read-only: exit 1 when a waiting file has a problem or a folder could not be read."""
    s = app.file_checker.run(_scope(app, p))
    return ModuleOutcome("FILE_CHECK", s, 1 if s.failed or s.errors else 0)


def _file_rules(app, p: dict) -> ModuleOutcome:
    """A file that fails its rules is audited and emailed (exit 0); exit 1 = the rules engine could not run."""
    s = app.rules_runner.run(_scope(app, p))
    return ModuleOutcome("FILE_RULES", s, 1 if s.errors else 0)


def _override_decisions(app, p: dict) -> ModuleOutcome:
    s = app.decisions.run(_scope(app, p))
    return ModuleOutcome("OVERRIDE_DECISIONS", s, 1 if s.invalid else 0)


def _batch_close(app, p: dict) -> ModuleOutcome:
    """Batches without data (waiting) and locked ones (deferred, closed by the next sweep) are normal."""
    s = app.closer.run(_scope(app, p), hold=run_type_list(p.get("hold")))
    return ModuleOutcome("BATCH_CLOSE", s, 0)


def _notify(app, p: dict) -> ModuleOutcome:
    """Events whose email failed stay unsent for the next run; exit 1 reports them."""
    notifier = app.notifier()
    sent = notifier.run(scope=_scope(app, p))
    return ModuleOutcome("NOTIFY", {"sent": sent, "failed": len(notifier.failed)}, 1 if notifier.failed else 0)


MODULES: dict[str, ModuleSpec] = {m.name: m for m in (
    ModuleSpec(
        "BATCH_CREATION",
        "one project's batches: the scheduled batches that are due by each run type's Batch_Schedule_Sql_Txt, with "
        "the report dates of each crosswalk row's Rpt_Dt_Sql_Txt (or for --period when given), and that project's "
        "pending ad-hoc intake requests - both in one call, scoped to --project",
        required=(Param("project"),),
        optional=(*SCOPE, Param("period"), Param("period_file"),
                  Param("lookback_days", int), Param("lookback_weeks", int)),
        handler=_batch_creation),
    ModuleSpec(
        "FILE_LOAD", "load inbound files: one file (--share --file), one folder (--share --folder), "
                     "one project's configured folders (--project) or every configured folder (no arguments)",
        required=(), optional=(Param("share"), Param("file"), Param("folder"), Param("project"), *SCOPE),
        handler=_file_load),
    ModuleSpec(
        "SOURCE_LOAD", "pull internal sources (ComplianceInternalSourceConfig) by batch: the open batches of the scope "
                       "that have no data yet, or the batches named in --batch (comma separated; a new pull replaces "
                       "the batch's rows). Scheduled run types wait for their due date, ad-hoc ones for "
                       "--release true; --hold (run types) holds them until released",
        required=(), optional=(Param("project"), *SCOPE, Param("batch"), Param("release"), Param("hold")), handler=_source_load),
    ModuleSpec(
        "EXPORT_LOAD", "copy each batch whose data changed (load, carry-forward, late arrival, correction) from core "
                       "into its export table (ComplianceExportConfig): the batch's rows replace the earlier rows of "
                       "that source and report period; a carried-forward batch gets a copy of the reused rows under "
                       "its own batch; --batch exports named batches again; scheduled run types wait "
                       "for their due date, ad-hoc ones for --release true (which also lets BATCH_CLOSE close them); --hold (run types) "
                       "holds them until released",
        required=(), optional=(Param("project"), *SCOPE, Param("batch"), Param("release"), Param("hold")), handler=_export_load),
    ModuleSpec(
        "FILE_CHECK", "optional check of the files waiting in the inbound folders, before FILE_LOAD: name, batch, "
                      "header, trailer, record types and column counts. It loads nothing, moves no file and changes "
                      "no batch; one project (--project) or every project",
        required=(), optional=(Param("project"), *SCOPE), handler=_file_check),
    ModuleSpec(
        "FILE_RULES", "run the bound rules on the files already loaded to core that have not had them yet; "
                      "one project (--project) or every project. It never blocks or undoes a load",
        required=(), optional=(Param("project"), *SCOPE), handler=_file_rules),
    ModuleSpec(
        "OVERRIDE_DECISIONS", "apply approved REUSE overrides and expire the ones that ran out; "
                              "one project (--project) or every project",
        required=(), optional=(Param("project"), *SCOPE), handler=_override_decisions),
    ModuleSpec(
        "BATCH_CLOSE", "close the batches past their SLA hold that have data or are in exception; "
                       "scope --project / --table / --run-type, or every batch; --hold leaves those run types' "
                       "batches open unless released",
        required=(), optional=(Param("project"), *SCOPE, Param("hold")), handler=_batch_close),
    ModuleSpec(
        "NOTIFY", "email pending events: one project's (--project), or every event including those of no project",
        required=(), optional=(Param("project"), *SCOPE), handler=_notify),
)}


def module_names() -> list[str]:
    return sorted(MODULES)


def resolve_module(name: str) -> ModuleSpec:
    spec = MODULES.get(normalize(name))
    if spec is None:
        raise ConfigError(f"unknown module {name!r}; available: {', '.join(module_names())}")
    return spec


def describe_modules() -> list[dict]:
    """What `framework list-modules` prints."""
    return [{"module": m.name, "description": m.description,
             "required": [p.name for p in m.required], "optional": [p.name for p in m.optional]}
            for m in MODULES.values()]


def _coerce(spec: ModuleSpec, params: Mapping[str, Any]) -> dict[str, Any]:
    known = spec.params
    out: dict[str, Any] = {}
    unknown = []
    for raw_name, value in params.items():
        name = _key(raw_name)
        if value is None or value == "":
            continue
        if name not in known:
            unknown.append(raw_name)
            continue
        kind = known[name].kind
        try:
            out[name] = kind(value) if kind is not str else str(value)
            if name in CODE_PARAMS:
                out[name] = code(out[name])
        except (TypeError, ValueError):
            raise ConfigError(f"module {spec.name}: {name} must be {kind.__name__}, got {value!r}") from None
    if unknown:
        raise ConfigError(f"module {spec.name} does not take: {', '.join(sorted(unknown))}; "
                          f"accepted: {', '.join(known) or 'no parameters'}")
    missing = [p.name for p in spec.required if p.name not in out]
    if missing:
        raise ConfigError(f"module {spec.name} needs: {', '.join(missing)}")
    return out


def run_module(app, name: str, params: Optional[Mapping[str, Any]] = None) -> ModuleOutcome:
    """Identify the module by name, validate its parameters and run it on `app`."""
    spec = resolve_module(name)
    return spec.handler(app, _coerce(spec, params or {}))
