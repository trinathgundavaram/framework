"""The configuration-load Glue job (module/aws/.../common/glue_job_metadata_load.py), with AWS stubbed."""
import importlib.util
import io
import pathlib
import sys
import types

import pytest

CODE = pathlib.Path(__file__).parents[1] / "module/aws/compliance_frameworks/compliance_batch_framework/common"
pytestmark = pytest.mark.skipif(not CODE.exists(), reason="deployment module not in this checkout")


class FakeS3:
    def __init__(self, body: bytes):
        self.body = body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.body)}


class FakeCursor:
    def __init__(self, log):
        self.log, self.rowcount = log, 0

    def execute(self, sql, params):
        self.log.append((sql, params))
        self.rowcount = len(params)

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.log, self.committed = [], False

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.committed = True


@pytest.fixture
def job(monkeypatch):
    utils = types.ModuleType("awsglue.utils")
    utils.getResolvedOptions = lambda argv, names: {}
    monkeypatch.setitem(sys.modules, "awsglue", types.ModuleType("awsglue"))
    monkeypatch.setitem(sys.modules, "awsglue.utils", utils)
    monkeypatch.setitem(sys.modules, "pg8000", types.ModuleType("pg8000"))
    monkeypatch.syspath_prepend(str(CODE))
    spec = importlib.util.spec_from_file_location("glue_job_metadata_load", CODE / "glue_job_metadata_load.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def use_csv(job, monkeypatch, text: str):
    monkeypatch.setattr(job.boto3, "client", lambda *_a, **_k: FakeS3(text.encode("utf-8-sig")))


def test_upsert_skips_audit_columns_and_nulls_empty_values(job, monkeypatch):
    use_csv(job, monkeypatch, 'project_cd,project_desc,active_ind,created_by\nODR,"Part C, ODR",1,x\nUNI,,1,y\n\n')
    conn = FakeConn()
    n = job.upsert_file(conn, "cms.complianceproject", "s3://b/seed/", "p.csv", ["project_cd"], "upsert",
                        job.DEFAULT_AUDIT_COLUMNS)
    (sql, params), = conn.log
    assert n == 2 and conn.committed
    assert sql.startswith("INSERT INTO cms.complianceproject (project_cd, project_desc, active_ind) VALUES")
    assert "ON CONFLICT (project_cd) DO UPDATE SET project_desc = EXCLUDED.project_desc" in sql
    assert "updated_dtts = now()" in sql
    assert params == ["ODR", "Part C, ODR", "1", "UNI", None, "1"]


def test_upsert_rejects_bad_input(job, monkeypatch):
    use_csv(job, monkeypatch, "project_cd,project_desc\nODR,x,extra\n")
    with pytest.raises(ValueError, match="do not have 2 values"):
        job.upsert_file(FakeConn(), "t", "s3://b/p/", "f.csv", ["project_cd"], "upsert", set())
    use_csv(job, monkeypatch, "project_cd,bad col\nODR,x\n")
    with pytest.raises(ValueError, match="not a plain SQL identifier"):
        job.upsert_file(FakeConn(), "t", "s3://b/p/", "f.csv", ["project_cd"], "upsert", set())
    with pytest.raises(ValueError, match="unknown --MODE"):
        job.upsert_file(FakeConn(), "t", "s3://b/p/", "f.csv", ["project_cd"], "merge", set())


def test_insert_only_does_nothing_on_conflict(job, monkeypatch):
    use_csv(job, monkeypatch, "src_id,src_nm\n50,Qnxt\n")
    conn = FakeConn()
    job.upsert_file(conn, "t", "s3://b/p/", "f.csv", ["src_id"], "insert_only", set())
    assert conn.log[0][0].endswith("ON CONFLICT (src_id) DO NOTHING")
