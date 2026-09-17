"""Adapters to external systems: object storage (S3 / local), the GRE rules engine and notification
channels (log / SES + SNS). Heavy SDKs are imported lazily.

The framework does not call the extract job itself (D-76): the project's job chain generates the
extract after the framework closes the run."""
from __future__ import annotations

import hashlib
import importlib
import logging
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

import psycopg

from .common import ConfigError, RuleEngineNotConfigured
from .settings import Settings

log = logging.getLogger(__name__)


# ============================================================================ object storage
@dataclass(frozen=True)
class ObjectInfo:
    bucket: str
    key: str
    version_id: Optional[str]
    etag: str
    size: int


def parse_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/prefix/ -> (bucket, 'prefix/'). Prefix always ends with '/' unless empty."""
    p = urlparse(uri)
    if p.scheme not in ("s3", "local"):
        raise ValueError(f"unsupported storage URI {uri!r}")
    prefix = p.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return p.netloc, prefix


def basename(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def dirname(key: str) -> str:
    return key.rsplit("/", 1)[0] + "/" if "/" in key else ""


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class ObjectStore(ABC):
    @abstractmethod
    def head(self, bucket: str, key: str, version_id: Optional[str] = None) -> ObjectInfo: ...

    @abstractmethod
    def exists(self, bucket: str, key: str) -> bool: ...

    @abstractmethod
    def download(self, bucket: str, key: str, dest: str, version_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str,
             version_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def delete(self, bucket: str, key: str) -> None: ...

    def move(self, src_bucket: str, src_key: str, dst_uri: str, version_id: Optional[str] = None,
             sub_prefix: str = "") -> str:
        """Copy to dst_uri/<sub_prefix>/<basename> then delete the source. Returns the destination key."""
        b, prefix = parse_uri(dst_uri)
        dst_key = f"{prefix}{sub_prefix}{basename(src_key)}"
        self.copy(src_bucket, src_key, b, dst_key, version_id)
        self.delete(src_bucket, src_key)
        return dst_key


class LocalObjectStore(ObjectStore):
    """<root>/<bucket>/<key>. ETag = md5 of content; no versioning."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        p = (self.root / bucket / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
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
        return ObjectInfo(bucket, key, None, hashlib.md5(p.read_bytes()).hexdigest(), p.stat().st_size)

    def exists(self, bucket, key):
        return self._path(bucket, key).is_file()

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
        return ObjectInfo(bucket, key, r.get("VersionId") if r.get("VersionId") != "null" else None,
                          r["ETag"].strip('"'), r["ContentLength"])

    def exists(self, bucket, key):
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def download(self, bucket, key, dest, version_id=None):
        extra = {"VersionId": version_id} if version_id else None
        self.s3.download_file(bucket, key, dest, ExtraArgs=extra)

    def copy(self, src_bucket, src_key, dst_bucket, dst_key, version_id=None):
        src = {"Bucket": src_bucket, "Key": src_key}
        if version_id:
            src["VersionId"] = version_id
        self.s3.copy({**src}, dst_bucket, dst_key,
                     ExtraArgs={"ServerSideEncryption": "aws:kms"})

    def delete(self, bucket, key):
        self.s3.delete_object(Bucket=bucket, Key=key)


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store == "local":
        return LocalObjectStore(settings.local_store_root)
    if settings.object_store == "s3":
        return S3ObjectStore(settings.aws_region)
    raise ValueError(f"unknown object store {settings.object_store!r}")


# ============================================================================ rules engine (D-08, D-43, D-44, D-63)
# GRE call mechanics are open question Q-12: the adapter delegates rule execution to a configurable
# entry point (GRE_ENTRYPOINT="package.module:function") with this contract:
#     def run_rules(conn, rule_group: str, rule_variant: str, run_params: dict) -> list[dict]
#         # one dict per executed rule: {"rule_ref": str, "passed": bool, "detail": str | None}
#         # raise any exception for a technical failure
# The framework applies GATE/ANNOTATE itself (FILE_RULES_MODE / PERIOD_RULES_MODE job settings).
PASSED, PASSED_WITH_WARNINGS, FAILED, ERROR = "PASSED", "PASSED_WITH_WARNINGS", "FAILED", "ERROR"


@dataclass
class RuleOutcome:
    status: str
    failed_rules: list[str] = field(default_factory=list)      # GATE failures
    warned_rules: list[str] = field(default_factory=list)      # ANNOTATE failures
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.status in (PASSED, PASSED_WITH_WARNINGS)


class RuleEngine(ABC):
    @abstractmethod
    def run(self, conn: psycopg.Connection, bindings: Sequence, run_params: dict, mode: str) -> RuleOutcome: ...


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
        except Exception as e:  # technical failure -> ERROR, never a business failure (E-23)
            log.exception("rule engine technical failure")
            return RuleOutcome(ERROR, error=str(e))
        failed = [r["rule_ref"] for r in results if not r.get("passed")]
        if not failed:
            return RuleOutcome(PASSED)
        if mode == "GATE":
            return RuleOutcome(FAILED, failed_rules=failed)
        return RuleOutcome(PASSED_WITH_WARNINGS, warned_rules=failed)


class NoRulesEngine(RuleEngine):
    """Passes everything. For environments where rules are intentionally disabled."""

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


# ============================================================================ notification channels (D-54)
@dataclass
class Message:
    subject: str
    body: str
    recipients: list[str]
    sns_topic_arn: Optional[str]


class Channel(ABC):
    @abstractmethod
    def send(self, channel_cd: str, msg: Message) -> None: ...


class LogChannel(Channel):
    def __init__(self):
        self.sent: list[tuple[str, Message]] = []

    def send(self, channel_cd, msg):
        self.sent.append((channel_cd, msg))
        log.warning("NOTIFY[%s] to=%s topic=%s | %s | %s", channel_cd, msg.recipients, msg.sns_topic_arn,
                    msg.subject, msg.body.replace("\n", " / "))


class AwsChannel(Channel):
    def __init__(self, settings: Settings):
        import boto3

        self.settings = settings
        self.ses = boto3.client("ses", region_name=settings.aws_region)
        self.sns = boto3.client("sns", region_name=settings.aws_region)

    def send(self, channel_cd, msg):
        if channel_cd in ("SES", "BOTH") and msg.recipients:
            if not self.settings.notify_from_email:
                raise RuntimeError("NOTIFY_FROM_EMAIL is required for SES")
            self.ses.send_email(Source=self.settings.notify_from_email, Destination={"ToAddresses": msg.recipients},
                                Message={"Subject": {"Data": msg.subject[:200]}, "Body": {"Text": {"Data": msg.body}}})
        if channel_cd in ("SNS", "BOTH") and msg.sns_topic_arn:
            self.sns.publish(TopicArn=msg.sns_topic_arn, Subject=msg.subject[:100], Message=msg.body)


def build_channel(settings: Settings) -> Channel:
    return AwsChannel(settings) if settings.notify_backend == "aws" else LogChannel()
