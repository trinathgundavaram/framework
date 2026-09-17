"""Object storage abstraction: S3 in AWS, a local directory tree for development and tests."""
from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..settings import Settings


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
