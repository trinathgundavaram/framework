"""Re-stage an approved reopen file from the S3 archive (D-53)."""
from __future__ import annotations

import os
import tempfile

import psycopg

from ..config.models import FileConfig
from ..errors import TechnicalFailure
from ..storage.object_store import ObjectStore, basename, parse_uri, sha256_file
from .engine import build_engine
from .tables import staging_business_columns


def restage(data_conn: psycopg.Connection, store: ObjectStore, cfg: FileConfig, load: dict, settings, now,
            target=None) -> int:
    bucket, prefix = parse_uri(cfg.src_file_archive_path)
    key = f"{prefix}{basename(load['s3_key'])}"
    if not store.exists(bucket, key):
        raise TechnicalFailure(f"archived object {bucket}/{key} not found")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, basename(key))
        store.download(bucket, key, path)
        if load["file_sha256"] and sha256_file(path) != load["file_sha256"]:
            raise TechnicalFailure(f"archived object {bucket}/{key} does not match the approved file checksum")
        engine = build_engine(cfg.engine_cd, settings)
        cols = staging_business_columns(data_conn, cfg.stg_schema_nm, cfg.stg_tblnm)
        res = engine.load_to_staging(data_conn, file_path=path, cfg=cfg, stg_columns=cols, btch_id=load["btch_id"],
                                     load_id=load["load_id"], src_file_nm=basename(load["s3_key"]),
                                     loaded_at=now, target=target)
    if res.data_rows != load["stg_rcd_cnt"]:
        raise TechnicalFailure(f"re-staged {res.data_rows} rows, expected {load['stg_rcd_cnt']}")
    return res.data_rows
