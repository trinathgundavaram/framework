"""Module dispatcher."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from . import config as cfgmod
from .common import ConfigError


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
    project, run_type = p["project"], p.get("run_type")
    period_args = ("period", "period_file", "lookback_days", "lookback_weeks")
    wants_period = any(k in p for k in period_args)
    scheduled = adhoc_run_ty = None
    if run_type:
        rt = cfgmod.run_type(app.conn, run_type)
        if rt is None or not rt.active:
            raise ConfigError(f"run type {run_type} is unknown or inactive")
        if rt.run_category_cd == "ROUTINE":
            if "period" not in p:
                raise ConfigError("BATCH_CREATION needs --period for a ROUTINE run type")
            scheduled = app.create_batches(project_cd=project, table_nm=p.get("table"), run_ty=run_type,
                                           period=p["period"], period_file=p.get("period_file"),
                                           lookback_days=p.get("lookback_days"),
                                           lookback_weeks=p.get("lookback_weeks"))
        else:
            if wants_period:
                raise ConfigError(f"run type {run_type} is ADHOC; --period/--period-file/--lookback-* "
                                  "only apply to a ROUTINE run type")
            adhoc_run_ty = run_type
    elif wants_period:
        raise ConfigError("BATCH_CREATION: --period/--period-file/--lookback-* need --run-type")
    adhoc = app.intake.run(project_cd=project, run_ty=adhoc_run_ty)
    errors = bool(scheduled and scheduled.errors) or bool(adhoc.failed)
    return ModuleOutcome("BATCH_CREATION", BatchCreationSummary(scheduled, adhoc), 1 if errors else 0)


def _file_load(app, p: dict) -> ModuleOutcome:
    bucket, key, prefix, project = p.get("bucket"), p.get("key"), p.get("prefix"), p.get("project")
    if project and (bucket or key or prefix):
        raise ConfigError("FILE_LOAD takes --project (that project's configured locations) or "
                          "--bucket/--key/--prefix, not both")
    if key:
        if not bucket:
            raise ConfigError("FILE_LOAD with --key also needs --bucket")
        if prefix:
            raise ConfigError("FILE_LOAD takes --key (one object) or --prefix (one location), not both")
        return ModuleOutcome("FILE_LOAD", app.pipeline.process_file(bucket, key, p.get("version_id")), 0)
    if p.get("version_id"):
        raise ConfigError("--version-id only applies to a single object (--key)")
    s = app.pipeline.process_path(bucket, prefix, project)
    return ModuleOutcome("FILE_LOAD", s, 1 if s.errors else 0)


def _file_rules(app, p: dict) -> ModuleOutcome:
    """A file that fails its rules is audited and emailed (exit 0); exit 1 = the rules engine could not run."""
    s = app.rules_runner.run(p.get("project"))
    return ModuleOutcome("FILE_RULES", s, 1 if s.errors else 0)


def _override_decisions(app, p: dict) -> ModuleOutcome:
    s = app.decisions.run(p.get("project"))
    return ModuleOutcome("OVERRIDE_DECISIONS", s, 1 if s.invalid else 0)


def _batch_close(app, p: dict) -> ModuleOutcome:
    """Batches without data (waiting) and locked ones (deferred, closed by the next sweep) are normal."""
    s = app.closer.run(p.get("project"), p.get("table"), p.get("run_type"))
    return ModuleOutcome("BATCH_CLOSE", s, 0)


def _notify(app, p: dict) -> ModuleOutcome:
    """Events whose email failed stay unsent for the next run; exit 1 reports them."""
    notifier = app.notifier()
    sent = notifier.run(project_cd=p.get("project"))
    return ModuleOutcome("NOTIFY", {"sent": sent, "failed": len(notifier.failed)}, 1 if notifier.failed else 0)


MODULES: dict[str, ModuleSpec] = {m.name: m for m in (
    ModuleSpec(
        "BATCH_CREATION",
        "one project's batches: routine batches for a ROUTINE run type and period, and that project's "
        "pending ad-hoc intake requests - both in one call, scoped to --project",
        required=(Param("project"),),
        optional=(Param("run_type"), Param("period"), Param("table"), Param("period_file"),
                  Param("lookback_days", int), Param("lookback_weeks", int)),
        handler=_batch_creation),
    ModuleSpec(
        "FILE_LOAD", "load inbound files: one object (--bucket --key), one location (--bucket --prefix), "
                     "one project's configured locations (--project) or every configured location (no arguments)",
        required=(), optional=(Param("bucket"), Param("key"), Param("prefix"), Param("version_id"), Param("project")),
        handler=_file_load),
    ModuleSpec(
        "FILE_RULES", "run the bound rules on the files already loaded to core that have not had them yet; "
                      "one project (--project) or every project. It never blocks or undoes a load",
        required=(), optional=(Param("project"),), handler=_file_rules),
    ModuleSpec(
        "OVERRIDE_DECISIONS", "apply approved REUSE overrides and expire the ones that ran out; "
                              "one project (--project) or every project",
        required=(), optional=(Param("project"),), handler=_override_decisions),
    ModuleSpec(
        "BATCH_CLOSE", "close the batches past their SLA hold that have data or are in exception; "
                       "scope --project / --table / --run-type, or every batch",
        required=(), optional=(Param("project"), Param("table"), Param("run_type")), handler=_batch_close),
    ModuleSpec(
        "NOTIFY", "email pending events: one project's (--project), or every event including those of no project",
        required=(), optional=(Param("project"),), handler=_notify),
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
