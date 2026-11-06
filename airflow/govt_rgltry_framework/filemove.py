"""FILE_MOVE: copies and moves files from a source folder to one or more target folders, by file name pattern.

Standalone: no batches, no metadata tables, no database. Each job lists its source folder (not its subfolders),
takes the files whose names match one of its patterns, and handles each file target by target, in order. Copies
come first; a job may end with one move, which removes the file from the source once every target has it. A file
is written to a temporary subfolder of the target, checked for size and only then given its name, so nothing
watching the target folder sees a half-written file. Any failure leaves the file in the source for the next run.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from govt_rgltry_framework import db
from govt_rgltry_framework.adapters import FileStore, basename, build_file_store, dirname, parse_uri
from govt_rgltry_framework.common import ConfigError
from govt_rgltry_framework.db import Settings

log = logging.getLogger(__name__)

STEP = "FILE_MOVE"
ACTIONS = ("copy", "move")
ON_EXISTS = ("fail", "rename", "overwrite")
TMP_FOLDER = "_file_move_tmp"


@dataclass(frozen=True)
class Target:
    folder: str
    share: str
    path: str
    action: str
    on_exists: str


@dataclass(frozen=True)
class Job:
    name: str
    src_folder: str
    share: str
    path: str
    patterns: tuple
    targets: tuple


class FileClash(Exception):
    """A file of that name is already in the target folder (on_exists fail)."""


def _list(value) -> list:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _location(folder: str, settings: Settings, what: str) -> tuple:
    resolved = settings.resolve_env(str(folder or "").strip(), False)
    if not resolved:
        raise ConfigError(f"{what} is required")
    share, path = parse_uri(resolved)
    return resolved, share, path


def parse_jobs(raw: Any, settings: Settings) -> list:
    """The Variable's `jobs`, checked; a ConfigError names the first problem."""
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'jobs' is required: a list of jobs (name, src_folder, patterns, targets)")
    jobs, names = [], set()
    for i, j in enumerate(raw, start=1):
        if not isinstance(j, dict):
            raise ConfigError(f"job {i} must be a JSON object")
        j = {str(k).lower(): v for k, v in j.items()}
        name = str(j.get("name") or "").strip()
        if not name:
            raise ConfigError(f"job {i}: 'name' is required")
        if name.upper() in names:
            raise ConfigError(f"job {name}: the name is used twice")
        names.add(name.upper())
        src, share, path = _location(j.get("src_folder"), settings, f"job {name}: 'src_folder'")
        patterns = tuple(_list(j.get("patterns")))
        if not patterns:
            raise ConfigError(f"job {name}: 'patterns' is required, e.g. [\"*.txt\"] or [\"CMSAUTH_*\"]")
        raw_targets = j.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ConfigError(f"job {name}: 'targets' is required: a list of {{folder, action}}")
        targets = []
        for k, t in enumerate(raw_targets, start=1):
            if not isinstance(t, dict):
                raise ConfigError(f"job {name}: target {k} must be a JSON object")
            t = {str(x).lower(): v for x, v in t.items()}
            folder, t_share, t_path = _location(t.get("folder"), settings, f"job {name}: target {k} 'folder'")
            action = str(t.get("action") or "").strip().lower()
            if action not in ACTIONS:
                raise ConfigError(f"job {name}: target {k} 'action' must be copy or move")
            on_exists = str(t.get("on_exists") or "fail").strip().lower()
            if on_exists not in ON_EXISTS:
                raise ConfigError(f"job {name}: target {k} 'on_exists' must be fail, rename or overwrite")
            if (t_share, t_path) == (share, path):
                raise ConfigError(f"job {name}: target {k} is the source folder")
            if action == "move" and k != len(raw_targets):
                raise ConfigError(f"job {name}: only the last target can be a move (target {k} is a move)")
            targets.append(Target(folder, t_share, t_path, action, on_exists))
        jobs.append(Job(name, src, share, path, patterns, tuple(targets)))
    return jobs


def matches(name: str, patterns) -> bool:
    """Whether a file name fits one of the patterns (* and ?, not case sensitive)."""
    return any(fnmatch.fnmatchcase(name.lower(), p.lower()) for p in patterns)


class FileMove:
    def __init__(self, store: FileStore):
        self.store = store
        self.run_id = uuid.uuid4().hex[:12]

    def _sha(self, share: str, path: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            return self.store.download(share, path, os.path.join(d, "f"))

    def _same(self, info, share: str, path: str) -> bool:
        """The target file is this very file (same size and content), e.g. from an earlier run that stopped."""
        if self.store.head(share, path).size != info.size:
            return False
        return self._sha(share, path) == self._sha(info.share, info.path)

    def _renamed(self, info, share: str, dst: str) -> tuple:
        """on_exists rename: (an earlier renamed copy with the same content or None, the next free name)."""
        name = basename(dst)
        stem, ext = os.path.splitext(name)
        n = 1
        while True:
            candidate = f"{dirname(dst)}{stem}_{n}{ext}"
            if not self.store.exists(share, candidate):
                return None, candidate
            if self._same(info, share, candidate):
                return candidate, candidate
            n += 1

    def _put(self, info, t: Target) -> tuple:
        """One target of one file -> (what happened, whether the source file is gone)."""
        name = basename(info.path)
        dst = f"{t.path}{name}"
        where = self.store.uri(t.share, t.path)
        if self.store.exists(t.share, dst):
            if self._same(info, t.share, dst):
                return f"already in {where} (same content)", False
            if t.on_exists == "fail":
                raise FileClash(f"{name} is already in {where} (on_exists fail)")
            if t.on_exists == "rename":
                earlier, dst = self._renamed(info, t.share, dst)
                if earlier:
                    return f"already in {where} as {basename(earlier)} (same content)", False
        verb = "moved" if t.action == "move" else "copied"
        target = self.store.uri(t.share, dst)
        if t.action == "move" and t.share == info.share:
            self.store.rename(info.share, info.path, dst)
            return f"{verb} to {target}", True
        tmp_folder = f"{t.path}{TMP_FOLDER}/"
        tmp = f"{tmp_folder}{self.run_id}_{name}"
        self.store.copy(info.share, info.path, t.share, tmp)
        try:
            size = self.store.head(t.share, tmp).size
            if size != info.size:
                raise OSError(f"copy of {name} to {where} has {size} bytes, the source {info.size}")
            self.store.rename(t.share, tmp, dst)
        except Exception:
            try:
                self.store.delete(t.share, tmp)
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            self.store.remove_folder(t.share, tmp_folder)
        return f"{verb} to {target}", False

    def _file(self, job: Job, info) -> dict:
        name = basename(info.path)
        out = {"file": name, "result": "", "targets": []}
        gone = False
        for t in job.targets:
            try:
                what, gone = self._put(info, t)
            except FileClash as e:
                return self._failed(job, out, t, str(e))
            except Exception as e:  # noqa: BLE001
                return self._failed(job, out, t, db.error_text(e))
            out["targets"].append({"folder": t.folder, "action": t.action, "outcome": what})
            log.info("%s | job %s | %s | %s", STEP, job.name, name, what)
        if job.targets[-1].action == "move" and not gone:
            try:
                self.store.delete(info.share, info.path)
            except Exception as e:  # noqa: BLE001
                out["result"], out["problem"] = "failed", f"in every target, but not removed from the source: {db.error_text(e)}"
                log.error("%s | job %s | %s | failed: %s", STEP, job.name, name, out["problem"])
                return out
        out["result"] = "moved" if job.targets[-1].action == "move" else "copied"
        return out

    def _failed(self, job: Job, out: dict, t: Target, problem: str) -> dict:
        out["targets"].append({"folder": t.folder, "action": t.action, "outcome": f"failed: {problem}"})
        out["result"], out["problem"] = "failed", f"{t.action} to {t.folder}: {problem}; the file stays in the source"
        log.error("%s | job %s | %s | failed: %s", STEP, job.name, out["file"], out["problem"])
        return out

    def run_job(self, job: Job) -> dict:
        out = {"name": job.name, "src_folder": job.src_folder, "patterns": list(job.patterns), "matched": 0,
               "moved": 0, "copied": 0, "failed": 0, "files": []}
        where = self.store.uri(job.share, job.path)
        try:
            listed = self.store.list_files(job.share, job.path)
        except Exception as e:  # noqa: BLE001
            out["problem"] = f"could not list {where}: {db.error_text(e)}"
            log.error("%s | job %s | %s", STEP, job.name, out["problem"])
            return out
        found = [f for f in listed if matches(basename(f.path), job.patterns)]
        out["matched"] = len(found)
        log.info("%s | job %s | %s | %s of %s file(s) match %s", STEP, job.name, where, len(found), len(listed),
                 ", ".join(job.patterns))
        for info in found:
            result = self._file(job, info)
            out["files"].append(result)
            out[result["result"]] += 1
        return out


def run_file_move(raw_jobs: Any, settings: Optional[Mapping[str, Any]] = None, only: Optional[str] = None,
                  log_level: str = "INFO") -> tuple:
    """Run the jobs (only the named ones when `only` is given) -> (report, exit code 0 / 1 / 2).

    1 = a file could not be copied or moved (it stays in the source); 2 = the step could not run (configuration,
    or a source folder that cannot be listed), retried by Airflow."""
    try:
        logging.getLogger("govt_rgltry_framework").setLevel(str(log_level or "INFO").upper())
        resolved = Settings.load(env=db.Env(), overrides={k: str(v) for k, v in (settings or {}).items()})
        jobs = parse_jobs(raw_jobs, resolved)
        names = [n.upper() for n in _list(only)]
        if names:
            unknown = [n for n in names if n not in {j.name.upper() for j in jobs}]
            if unknown:
                raise ConfigError(f"job {unknown[0]} is not in jobs ({', '.join(j.name for j in jobs)})")
            jobs = [j for j in jobs if j.name.upper() in names]
        mover = FileMove(build_file_store(resolved))
        results = [mover.run_job(j) for j in jobs]
    except (ConfigError, ValueError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return {"step": STEP, "result": "COULD NOT RUN", "problem": f"{type(e).__name__}: {e}"}, 2
    except Exception as e:  # noqa: BLE001
        log.exception("%s failed", STEP)
        return {"step": STEP, "result": "COULD NOT RUN", "problem": db.error_text(e)}, 2
    totals = {k: sum(r[k] for r in results) for k in ("matched", "moved", "copied", "failed")}
    unlisted = [r["name"] for r in results if r.get("problem")]
    code = 2 if unlisted else 1 if totals["failed"] else 0
    result = "COULD NOT RUN" if unlisted else "COMPLETED WITH PROBLEMS" if totals["failed"] else "COMPLETED"
    report = {"step": STEP, "result": result, **totals, "handled": totals["moved"] + totals["copied"], "jobs": results}
    if unlisted:
        report["problem"] = f"source folder of job(s) {', '.join(unlisted)} could not be listed"
    return report, code
