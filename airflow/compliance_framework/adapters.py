"""Adapters to external systems: object store, rules engine, email."""
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
class ObjectInfo:
    bucket: str
    key: str
    version_id: Optional[str]
    etag: str
    size: int


def parse_uri(uri: str) -> tuple[str, str]:
    """A file location -> (bucket, 'folder/').

    NAS: \\\\server\\share\\folder, //server/share/folder or smb://server/share/folder -> ('server/share', 'folder/');
    nas://share/folder -> ('share', 'folder/') on the server of the NAS connection. Also s3://bucket/prefix/.
    """
    text = (uri or "").strip().replace("\\", "/")
    p = urlparse(text)
    if text.startswith("//") or p.scheme == "smb":
        share, _, folder = p.path.lstrip("/").partition("/")
        if not p.netloc or not share:
            raise ValueError(f"NAS path {uri!r} must name a server and a share")
        bucket = f"{p.netloc.lower()}/{share.lower()}"
    elif p.scheme == "nas" and p.netloc:
        bucket, folder = p.netloc.lower(), p.path
    elif p.scheme in ("s3", "local") and p.netloc:
        bucket, folder = p.netloc, p.path
    else:
        raise ValueError(f"unsupported file location {uri!r}: use \\\\server\\share\\folder, "
                         "nas://share/folder or s3://bucket/prefix/")
    folder = "/".join(x for x in folder.split("/") if x)
    return bucket, folder + "/" if folder else ""


def basename(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def dirname(key: str) -> str:
    return key.rsplit("/", 1)[0] + "/" if "/" in key else ""


def _hash_file(path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: str) -> str:
    return _hash_file(path, "sha256")


class ObjectStore(ABC):
    @abstractmethod
    def head(self, bucket: str, key: str, version_id: Optional[str] = None) -> ObjectInfo: ...

    @abstractmethod
    def exists(self, bucket: str, key: str) -> bool: ...

    @abstractmethod
    def list_objects(self, bucket: str, prefix: str) -> list[ObjectInfo]: ...

    @abstractmethod
    def download(self, bucket: str, key: str, dest: str, version_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str,
             version_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def delete(self, bucket: str, key: str) -> None: ...

    def uri(self, bucket: str, key: str) -> str:
        return f"s3://{bucket}/{key}"

    def archive(self, bucket: str, key: str, folder: str, version_id: Optional[str] = None) -> str:
        """Move a file into <its own folder>/<folder>/; returns the new key."""
        dst_key = f"{dirname(key)}{folder}/{basename(key)}"
        self.copy(bucket, key, bucket, dst_key, version_id)
        self.delete(bucket, key)
        return dst_key


class LocalObjectStore(ObjectStore):
    """<root>/<bucket>/<key>."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        p = (self.root / bucket / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("path escapes the local store root")
        return p

    def put(self, bucket: str, key: str, data: bytes) -> None:
        p = self._path(bucket, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def head(self, bucket, key, version_id=None):
        p = self._path(bucket, key)
        if not p.is_file():
            raise FileNotFoundError(f"local://{bucket}/{key}")
        return ObjectInfo(bucket, key, None, _hash_file(p, "md5"), p.stat().st_size)

    def exists(self, bucket, key):
        return self._path(bucket, key).is_file()

    def list_objects(self, bucket, prefix):
        """Files directly under <bucket>/<prefix>, not recursive."""
        d = self._path(bucket, prefix)
        if not d.is_dir():
            return []
        return [ObjectInfo(bucket, f"{prefix}{p.name}", None, _hash_file(p, "md5"), p.stat().st_size)
                for p in sorted(d.iterdir()) if p.is_file()]

    def download(self, bucket, key, dest, version_id=None):
        shutil.copyfile(self._path(bucket, key), dest)

    def copy(self, src_bucket, src_key, dst_bucket, dst_key, version_id=None):
        dst = self._path(dst_bucket, dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(src_bucket, src_key), dst)

    def delete(self, bucket, key):
        p = self._path(bucket, key)
        if p.exists():
            os.remove(p)


class S3ObjectStore(ObjectStore):
    def __init__(self, region: str):
        import boto3

        self.s3 = boto3.client("s3", region_name=region)

    def head(self, bucket, key, version_id=None):
        kw = {"Bucket": bucket, "Key": key}
        if version_id:
            kw["VersionId"] = version_id
        r = self.s3.head_object(**kw)
        etag = r["ETag"].strip('"')
        return ObjectInfo(bucket, key, r.get("VersionId") if r.get("VersionId") != "null" else None,
                          f"{etag}-{r['LastModified']:%Y%m%d%H%M%S}", r["ContentLength"])

    def exists(self, bucket, key):
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list_objects(self, bucket, prefix):
        """Objects directly under prefix, not recursive."""
        out: list[ObjectInfo] = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            for obj in page.get("Contents", []):
                if obj["Key"] == prefix:
                    continue
                out.append(ObjectInfo(bucket, obj["Key"], None, obj["ETag"].strip('"'), obj["Size"]))
        return out

    def download(self, bucket, key, dest, version_id=None):
        extra = {"VersionId": version_id} if version_id else None
        self.s3.download_file(bucket, key, dest, ExtraArgs=extra)

    def copy(self, src_bucket, src_key, dst_bucket, dst_key, version_id=None):
        src = {"Bucket": src_bucket, "Key": src_key}
        if version_id:
            src["VersionId"] = version_id
        self.s3.copy(src, dst_bucket, dst_key, ExtraArgs={"ServerSideEncryption": "aws:kms"})

    def delete(self, bucket, key):
        self.s3.delete_object(Bucket=bucket, Key=key)


_SMB_NOT_FOUND = {0xC000000F, 0xC0000034, 0xC000003A}


class NasObjectStore(ObjectStore):
    """Files on an SMB share; bucket = '<server>/<share>', or '<share>' on the server of the NAS connection."""

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

    def _target(self, bucket: str) -> tuple[str, str]:
        server, _, share = bucket.rpartition("/")
        return server or self.server, share

    def _path(self, bucket: str, key: str) -> str:
        server, share = self._target(bucket)
        parts = [p for p in key.split("/") if p]
        if not share or ".." in parts:
            raise ValueError(f"invalid NAS path {bucket}/{key}")
        return "\\\\" + "\\".join([server, share, *parts])

    @staticmethod
    def _info(bucket: str, key: str, size: int, modified: datetime) -> ObjectInfo:
        return ObjectInfo(bucket, key, None, f"{size}-{modified:%Y%m%d%H%M%S%f}", size)

    def uri(self, bucket, key):
        return "//" + "/".join([*self._target(bucket), key])

    def head(self, bucket, key, version_id=None):
        st = self.smb.stat(self._path(bucket, key), **self.nas)
        return self._info(bucket, key, st.st_size, datetime.fromtimestamp(st.st_mtime_ns // 1000 / 1e6, timezone.utc))

    def exists(self, bucket, key):
        try:
            st = self.smb.stat(self._path(bucket, key), **self.nas)
        except OSError as e:
            if e.errno == errno.ENOENT or getattr(e, "ntstatus", None) in _SMB_NOT_FOUND:
                return False
            raise
        return stat.S_ISREG(st.st_mode)

    def list_objects(self, bucket, prefix):
        """Files directly in the folder; one changed in the last NAS_MIN_AGE_SECONDS may still be arriving and waits."""
        newest = datetime.now(timezone.utc) - timedelta(seconds=self.min_age_seconds)
        out = []
        for entry in self.smb.scandir(self._path(bucket, prefix), **self.nas):
            if entry.is_file():
                size, modified = entry.smb_info.end_of_file, entry.smb_info.last_write_time
                if modified <= newest:
                    out.append(self._info(bucket, f"{prefix}{entry.name}", size, modified))
        return sorted(out, key=lambda o: o.key)

    def download(self, bucket, key, dest, version_id=None):
        with self.smb.open_file(self._path(bucket, key), mode="rb", **self.nas) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)

    def copy(self, src_bucket, src_key, dst_bucket, dst_key, version_id=None):
        self.smb.makedirs(self._path(dst_bucket, dirname(dst_key)), exist_ok=True, **self.nas)
        with self.smb.open_file(self._path(src_bucket, src_key), mode="rb", **self.nas) as src, \
                self.smb.open_file(self._path(dst_bucket, dst_key), mode="wb", **self.nas) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)

    def delete(self, bucket, key):
        self.smb.remove(self._path(bucket, key), **self.nas)

    def archive(self, bucket, key, folder, version_id=None):
        dst_key = f"{dirname(key)}{folder}/{basename(key)}"
        self.smb.makedirs(self._path(bucket, dirname(dst_key)), exist_ok=True, **self.nas)
        try:
            self.smb.replace(self._path(bucket, key), self._path(bucket, dst_key), **self.nas)
        except OSError as e:
            log.info("rename of %s not possible (%s); copying instead", key, e)
            self.copy(bucket, key, bucket, dst_key)
            self.delete(bucket, key)
        return dst_key


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store == "nas":
        return NasObjectStore(settings)
    if settings.object_store == "local":
        return LocalObjectStore(settings.local_store_root)
    if settings.object_store == "s3":
        return S3ObjectStore(settings.aws_region)
    raise ValueError(f"unknown object store {settings.object_store!r}")


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
