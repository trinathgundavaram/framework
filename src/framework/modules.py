"""Module dispatcher: run one framework module by name.

One entry point serves every kind of job. The caller passes a **module name** and that module's
parameters; the dispatcher identifies the module, checks the parameters and calls the owning service.
A single Glue job / Step Functions state / cron line can therefore run any module:

    framework run --module BATCH_CREATION --project PRJA --run-type MONTHLY --period PREV_CALENDAR_MONTH
    framework run --module FILE_LOAD --bucket inbound --key prja/in/<file>      # one object
    framework run --module FILE_LOAD --bucket inbound --prefix prja/in/         # one location
    framework run --module FILE_LOAD                                            # every configured location
    framework run --module RULES_TRIGGER --project PRJA --run-type MONTHLY      # open extracts of a scope
    framework run --module RULES_TRIGGER --extract-id 12                        # one extract

From code (a Lambda, a notebook, another job):

    from framework.modules import run_module
    outcome = run_module(app, "batch-creation", {"project": "PRJA", "run_type": "MONTHLY", "period": "..."})
    outcome.result, outcome.exit_code

Modules
  BATCH_CREATION  create the batches of one project / ROUTINE run type for the period of the run date
  BATCH_INTAKE    create the batches of the pending ad-hoc requests (ComplianceRequestInTake)
  FILE_LOAD       load inbound files: one object, one location, or every configured location
  RULES_TRIGGER   run the period-level rules of one extract, or of the open extracts of a scope

Names are case-insensitive and `-` / `_` are interchangeable (`file-load` = `FILE_LOAD`). An unknown
module, a missing required parameter or a parameter the module does not take is a ConfigError (exit 2),
so a typo never silently runs the wrong thing. The dispatcher holds no business logic and no SQL: it
only maps a name to the service that already owns the work (`batches`, `ingest`, `extract`), the same
split as `app.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .common import ConfigError


@dataclass
class ModuleOutcome:
    module: str          # canonical module name
    result: Any          # the service's summary object (dataclass) or dict
    exit_code: int       # 0 ok, 1 completed with problems


@dataclass(frozen=True)
class Param:
    name: str
    kind: type = str                 # str | int
    help: str = ""


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    description: str
    required: tuple[Param, ...]
    optional: tuple[Param, ...]
    handler: Callable[[Any, dict], ModuleOutcome]
    aliases: tuple[str, ...] = ()

    @property
    def params(self) -> dict[str, Param]:
        return {p.name: p for p in (*self.required, *self.optional)}


def normalize(name: str) -> str:
    return (name or "").strip().upper().replace("-", "_").replace(" ", "_")


def _key(name: str) -> str:
    return name.strip().lstrip("-").lower().replace("-", "_")


# ============================================================================ handlers
def _batch_creation(app, p: dict) -> ModuleOutcome:
    s = app.create_batches(project_cd=p["project"], table_nm=p.get("table"), run_ty=p["run_type"],
                           period=p["period"], period_file=p.get("period_file"),
                           lookback_days=p.get("lookback_days"), lookback_weeks=p.get("lookback_weeks"))
    return ModuleOutcome("BATCH_CREATION", s, 1 if s.errors else 0)


def _batch_intake(app, p: dict) -> ModuleOutcome:
    return ModuleOutcome("BATCH_INTAKE", app.intake.run(), 0)


def _file_load(app, p: dict) -> ModuleOutcome:
    bucket, key, prefix = p.get("bucket"), p.get("key"), p.get("prefix")
    if key:                                                   # one object
        if not bucket:
            raise ConfigError("FILE_LOAD with --key also needs --bucket")
        if prefix:
            raise ConfigError("FILE_LOAD takes --key (one object) or --prefix (one location), not both")
        return ModuleOutcome("FILE_LOAD", app.pipeline.process_file(bucket, key, p.get("version_id")), 0)
    if p.get("version_id"):
        raise ConfigError("--version-id only applies to a single object (--key)")
    s = app.pipeline.process_path(bucket, prefix)             # one location, or every configured one
    return ModuleOutcome("FILE_LOAD", s, 1 if s.errors else 0)


def _rules_trigger(app, p: dict) -> ModuleOutcome:
    if p.get("extract_id") is None and not p.get("project"):
        raise ConfigError("RULES_TRIGGER needs --extract-id, or --project (optionally --table / --run-type)")
    if p.get("extract_id") is not None and any(p.get(k) for k in ("project", "table", "run_type")):
        raise ConfigError("RULES_TRIGGER takes --extract-id or a project scope, not both")
    s = app.control.trigger_rules(extract_id=p.get("extract_id"), project_cd=p.get("project"),
                                  table_nm=p.get("table"), run_ty=p.get("run_type"))
    return ModuleOutcome("RULES_TRIGGER", s, 1 if (s.failed or s.errors) else 0)


# ============================================================================ registry
_SCOPE = (Param("project", help="project code"), Param("table", help="table name"),
          Param("run_type", help="run type"))

MODULES: dict[str, ModuleSpec] = {m.name: m for m in (
    ModuleSpec(
        "BATCH_CREATION", "create the batches of one project / ROUTINE run type for the period of the run date",
        required=(Param("project"), Param("run_type"), Param("period", help="name in period_sql.py")),
        optional=(Param("table"), Param("period_file", help="project .py file defining PERIOD_SQL"),
                  Param("lookback_days", int), Param("lookback_weeks", int)),
        handler=_batch_creation, aliases=("CREATE_BATCHES", "BATCHES", "BATCH")),
    ModuleSpec(
        "BATCH_INTAKE", "create the batches of the pending ad-hoc requests",
        required=(), optional=(), handler=_batch_intake, aliases=("INTAKE", "PROCESS_INTAKE", "ADHOC_BATCHES")),
    ModuleSpec(
        "FILE_LOAD", "load inbound files: one object (--bucket --key), one location (--bucket --prefix), "
                     "or every configured location (no arguments)",
        required=(), optional=(Param("bucket"), Param("key"), Param("prefix"), Param("version_id")),
        handler=_file_load, aliases=("INGEST", "FILE_INGEST", "LOAD", "INGEST_FILE", "INGEST_PATH")),
    ModuleSpec(
        "RULES_TRIGGER", "run the period-level rules of one extract, or of the open extracts of a scope",
        required=(), optional=(*_SCOPE, Param("extract_id", int)),
        handler=_rules_trigger, aliases=("RULES", "TRIGGER_RULES", "RUN_RULES")),
)}
_LOOKUP: dict[str, str] = {}
for _spec in MODULES.values():
    for _n in (_spec.name, *_spec.aliases):
        assert _LOOKUP.setdefault(normalize(_n), _spec.name) == _spec.name, f"duplicate module alias {_n}"


def module_names() -> list[str]:
    return sorted(MODULES)


def resolve_module(name: str) -> ModuleSpec:
    canonical = _LOOKUP.get(normalize(name))
    if canonical is None:
        raise ConfigError(f"unknown module {name!r}; available: {', '.join(module_names())}")
    return MODULES[canonical]


def describe_modules() -> list[dict]:
    """What `framework list-modules` prints: name, aliases, what it does and its parameters."""
    return [{"module": m.name, "aliases": sorted(m.aliases), "description": m.description,
             "required": [p.name for p in m.required], "optional": [p.name for p in m.optional]}
            for m in MODULES.values()]


def _coerce(spec: ModuleSpec, params: Mapping[str, Any]) -> dict[str, Any]:
    known = spec.params
    out: dict[str, Any] = {}
    unknown = []
    for raw_name, value in params.items():
        name = _key(raw_name)
        if value is None or value == "":
            continue                                           # an unset job argument is not a value
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
