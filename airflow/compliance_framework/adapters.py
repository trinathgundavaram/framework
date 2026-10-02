"""Adapters to external systems: file store (NAS), rules engine, email."""
from __future__ import annotations

import errno
import hashlib
import html
import importlib
import logging
import os
import shutil
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

from .common import ConfigError, RuleEngineNotConfigured
from .connection_factory import nas_settings, smb_client
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileInfo:
    share: str
    path: str
    version: str
    size: int


def parse_uri(uri: str) -> tuple[str, str]:
    """A file location -> (share, 'folder/').

    NAS: \\\\server\\share\\folder, //server/share/folder or smb://server/share/folder -> ('server/share', 'folder/');
    nas://share/folder -> ('share', 'folder/') on the server of the NAS connection.
    """
    text = (uri or "").strip().replace("\\", "/")
    p = urlparse(text)
    if text.startswith("//") or p.scheme == "smb":
        share, _, folder = p.path.lstrip("/").partition("/")
        if not p.netloc or not share:
            raise ValueError(f"NAS path {uri!r} must name a server and a share")
        share = f"{p.netloc.lower()}/{share.lower()}"
    elif p.scheme == "nas" and p.netloc:
        share, folder = p.netloc.lower(), p.path
    elif p.scheme == "local" and p.netloc:
        share, folder = p.netloc, p.path
    else:
        raise ValueError(f"unsupported file location {uri!r}: use \\\\server\\share\\folder "
                         "or nas://share/folder")
    folder = "/".join(x for x in folder.split("/") if x)
    return share, folder + "/" if folder else ""


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] + "/" if "/" in path else ""


def _hash_file(path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: str) -> str:
    return _hash_file(path, "sha256")


class FileStore(ABC):
    @abstractmethod
    def head(self, share: str, path: str) -> FileInfo: ...

    @abstractmethod
    def exists(self, share: str, path: str) -> bool: ...

    @abstractmethod
    def list_files(self, share: str, folder: str) -> list[FileInfo]: ...

    @abstractmethod
    def download(self, share: str, path: str, dest: str) -> None: ...

    @abstractmethod
    def copy(self, src_share: str, src_path: str, dst_share: str, dst_path: str) -> None: ...

    @abstractmethod
    def delete(self, share: str, path: str) -> None: ...

    def uri(self, share: str, path: str) -> str:
        return f"//{share}/{path}"

    def archive(self, share: str, path: str, folder: str) -> str:
        """Move a file into <its own folder>/<folder>/; returns the new path."""
        dst_path = f"{dirname(path)}{folder}/{basename(path)}"
        self.copy(share, path, share, dst_path)
        self.delete(share, path)
        return dst_path


class LocalFileStore(FileStore):
    """<root>/<share>/<path>."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, share: str, path: str) -> Path:
        p = (self.root / share / path).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("path escapes the local store root")
        return p

    def put(self, share: str, path: str, data: bytes) -> None:
        p = self._path(share, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def head(self, share, path):
        p = self._path(share, path)
        if not p.is_file():
            raise FileNotFoundError(f"local://{share}/{path}")
        return FileInfo(share, path, _hash_file(p, "md5"), p.stat().st_size)

    def exists(self, share, path):
        return self._path(share, path).is_file()

    def list_files(self, share, folder):
        """Files directly under <share>/<folder>, not recursive."""
        d = self._path(share, folder)
        if not d.is_dir():
            return []
        return [FileInfo(share, f"{folder}{p.name}", _hash_file(p, "md5"), p.stat().st_size)
                for p in sorted(d.iterdir()) if p.is_file()]

    def download(self, share, path, dest):
        shutil.copyfile(self._path(share, path), dest)

    def copy(self, src_share, src_path, dst_share, dst_path):
        dst = self._path(dst_share, dst_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(src_share, src_path), dst)

    def delete(self, share, path):
        p = self._path(share, path)
        if p.exists():
            os.remove(p)


_SMB_NOT_FOUND = {0xC000000F, 0xC0000034, 0xC000003A}


class NasFileStore(FileStore):
    """Files on an SMB share; share = '<server>/<share>', or '<share>' on the server of the NAS connection."""

    def __init__(self, settings: Settings):
        self.min_age_seconds = settings.nas_min_age_seconds

    @cached_property
    def smb(self):
        return smb_client()

    @cached_property
    def server(self) -> str:
        return nas_settings()["server"]

    @cached_property
    def nas(self) -> dict:
        """Session arguments of every smbclient call; read when the first file is touched."""
        return {k: v for k, v in nas_settings().items() if k != "server"}

    def _target(self, share: str) -> tuple[str, str]:
        server, _, share = share.rpartition("/")
        return server or self.server, share

    def _path(self, share: str, path: str) -> str:
        server, share = self._target(share)
        parts = [p for p in path.split("/") if p]
        if not share or ".." in parts:
            raise ValueError(f"invalid NAS path {share}/{path}")
        return "\\\\" + "\\".join([server, share, *parts])

    @staticmethod
    def _info(share: str, path: str, size: int, modified: datetime) -> FileInfo:
        return FileInfo(share, path, f"{size}-{modified:%Y%m%d%H%M%S%f}", size)

    def uri(self, share, path):
        return "//" + "/".join([*self._target(share), path])

    def head(self, share, path):
        st = self.smb.stat(self._path(share, path), **self.nas)
        return self._info(share, path, st.st_size, datetime.fromtimestamp(st.st_mtime_ns // 1000 / 1e6, timezone.utc))

    def exists(self, share, path):
        try:
            st = self.smb.stat(self._path(share, path), **self.nas)
        except OSError as e:
            if e.errno == errno.ENOENT or getattr(e, "ntstatus", None) in _SMB_NOT_FOUND:
                return False
            raise
        return stat.S_ISREG(st.st_mode)

    def list_files(self, share, folder):
        """Files directly in the folder; one changed in the last NAS_MIN_AGE_SECONDS may still be arriving and waits."""
        newest = datetime.now(timezone.utc) - timedelta(seconds=self.min_age_seconds)
        out = []
        for entry in self.smb.scandir(self._path(share, folder), **self.nas):
            if entry.is_file():
                size, modified = entry.smb_info.end_of_file, entry.smb_info.last_write_time
                if modified <= newest:
                    out.append(self._info(share, f"{folder}{entry.name}", size, modified))
        return sorted(out, key=lambda o: o.path)

    def download(self, share, path, dest):
        with self.smb.open_file(self._path(share, path), mode="rb", **self.nas) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)

    def copy(self, src_share, src_path, dst_share, dst_path):
        self.smb.makedirs(self._path(dst_share, dirname(dst_path)), exist_ok=True, **self.nas)
        with self.smb.open_file(self._path(src_share, src_path), mode="rb", **self.nas) as src, \
                self.smb.open_file(self._path(dst_share, dst_path), mode="wb", **self.nas) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)

    def delete(self, share, path):
        self.smb.remove(self._path(share, path), **self.nas)

    def archive(self, share, path, folder):
        dst_path = f"{dirname(path)}{folder}/{basename(path)}"
        self.smb.makedirs(self._path(share, dirname(dst_path)), exist_ok=True, **self.nas)
        try:
            self.smb.replace(self._path(share, path), self._path(share, dst_path), **self.nas)
        except OSError as e:
            log.info("rename of %s not possible (%s); copying instead", path, e)
            self.copy(share, path, share, dst_path)
            self.delete(share, path)
        return dst_path


def build_file_store(settings: Settings) -> FileStore:
    if settings.file_store == "nas":
        return NasFileStore(settings)
    if settings.file_store == "local":
        return LocalFileStore(settings.local_store_root)
    raise ValueError(f"unknown file store {settings.file_store!r}")


PASSED, PASSED_WITH_WARNINGS, FAILED, ERROR = "PASSED", "PASSED_WITH_WARNINGS", "FAILED", "ERROR"


@dataclass
class RuleOutcome:
    status: str
    failed_rules: list[str] = field(default_factory=list)
    warned_rules: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.status in (PASSED, PASSED_WITH_WARNINGS)


class RuleEngine(ABC):
    @abstractmethod
    def run(self, conn, bindings: Sequence, run_params: dict, mode: str) -> RuleOutcome: ...


class CallableRuleEngine(RuleEngine):
    """Runs each bound GRE group/variant through `fn` and applies the validation mode."""

    def __init__(self, fn: Callable[..., list[dict]]):
        self.fn = fn

    def run(self, conn, bindings, run_params, mode):
        results: list[dict] = []
        try:
            for b in bindings:
                for r in self.fn(conn, b.gre_rule_group, b.gre_rule_variant, dict(run_params)):
                    if "rule_ref" not in r or "passed" not in r:
                        raise ValueError(f"rule engine returned an invalid result {r!r}")
                    results.append(r)
        except Exception as e:
            log.exception("rule engine technical failure")
            return RuleOutcome(ERROR, error=str(e))
        failed = [r["rule_ref"] for r in results if not r.get("passed")]
        if not failed:
            return RuleOutcome(PASSED)
        if mode == "GATE":
            return RuleOutcome(FAILED, failed_rules=failed)
        return RuleOutcome(PASSED_WITH_WARNINGS, warned_rules=failed)


class NoRulesEngine(RuleEngine):
    """Passes everything."""

    def run(self, conn, bindings, run_params, mode):
        return RuleOutcome(PASSED)


def _import(ref: str, what: str):
    module, _, attr = ref.partition(":")
    if not module or not attr:
        raise ConfigError(f"{what} must be 'module:name', got {ref!r}")
    return getattr(importlib.import_module(module), attr)


def build_rule_engine(settings: Settings) -> RuleEngine:
    if settings.rule_engine == "none":
        return NoRulesEngine()
    if settings.rule_engine != "gre":
        return _import(settings.rule_engine, "RULE_ENGINE")()
    if not settings.gre_entrypoint:
        def missing(*_a, **_k):
            raise RuleEngineNotConfigured("GRE_ENTRYPOINT is not set (open question Q-12: GRE call mechanics)")
        return CallableRuleEngine(missing)
    return CallableRuleEngine(_import(settings.gre_entrypoint, "GRE_ENTRYPOINT"))


@dataclass
class Message:
    subject: str
    body: str
    recipients: list[str]


class Channel(ABC):
    @abstractmethod
    def send(self, msg: Message) -> None: ...


class LogChannel(Channel):
    """Writes emails to the log instead of sending them (local development, tests)."""

    def __init__(self):
        self.sent: list[Message] = []

    def send(self, msg):
        self.sent.append(msg)
        log.warning("EMAIL to=%s | %s | %s", msg.recipients, msg.subject, msg.body.replace("\n", " / "))


class SesChannel(Channel):
    def __init__(self, settings: Settings):
        import boto3

        if not settings.notify_from_email:
            raise ConfigError("NOTIFY_FROM_EMAIL is required when NOTIFY_BACKEND=ses")
        self.sender = settings.notify_from_email
        self.ses = boto3.client("ses", region_name=settings.aws_region)

    def send(self, msg):
        if not msg.recipients:
            log.warning("no recipients for %r; not sent", msg.subject)
            return
        self.ses.send_email(Source=self.sender, Destination={"ToAddresses": msg.recipients},
                            Message={"Subject": {"Data": msg.subject[:200]}, "Body": {"Text": {"Data": msg.body}}})


class AirflowEmailChannel(Channel):
    """Sends through Airflow's configured email backend (SMTP / SES)."""

    def send(self, msg):
        from airflow.utils.email import send_email

        if not msg.recipients:
            log.warning("no recipients for %r; not sent", msg.subject)
            return
        send_email(to=msg.recipients, subject=msg.subject[:200],
                   html_content="<pre>" + html.escape(msg.body) + "</pre>")


def build_channel(settings: Settings) -> Channel:
    if settings.notify_backend == "ses":
        return SesChannel(settings)
    if settings.notify_backend == "airflow":
        return AirflowEmailChannel()
    return LogChannel()
