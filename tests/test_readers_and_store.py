from dataclasses import replace

import pytest

from framework.errors import FileRejected
from framework.ingest.file_reader import read_file, scan_delimited
from framework.settings import Settings
from framework.storage.object_store import LocalObjectStore, parse_uri

from .test_templates import cfg


def settings(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_xlsx_reader(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    path = tmp_path / "f.xlsx"
    pd.DataFrame([["id", "amount", "name"], [1, 2.5, "a"], [2, None, "b"]]).to_excel(path, header=False, index=False)
    c = replace(cfg(), src_file_ty=".xlsx")
    with pytest.raises(FileRejected, match="not enabled"):
        read_file(str(path), c, 3, settings())
    r = read_file(str(path), c, 3, settings(supported_file_types=[".xlsx"]))
    assert r.rows == [["1", "2.5", "a"], ["2", None, "b"]]
    with pytest.raises(FileRejected) as e:
        read_file(str(path), c, 4, settings(supported_file_types=[".xlsx"]))
    assert e.value.event_ty == "FILE_COLUMN_COUNT_MISMATCH"


def test_delimiters_encoding_and_streaming_scan(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes("a\tb\n1\t2\n".encode())
    c = replace(cfg(), delmtr_cd="TAB")
    assert read_file(str(p), c, 2, settings()).rows == [["1", "2"]]
    assert scan_delimited(str(p), c, 2, settings()) == 1
    with pytest.raises(FileRejected):
        scan_delimited(str(p), c, 3, settings())
    p.write_bytes(b"a\tb\n\xff\xfe\t2\n")
    with pytest.raises(FileRejected) as e:
        read_file(str(p), c, 2, settings())
    assert e.value.event_ty == "FILE_PARSE_ERROR"
    assert read_file(str(p), c, 2, settings(file_encoding="latin-1")).data_row_count == 1


def test_local_store(tmp_path):
    s = LocalObjectStore(str(tmp_path))
    s.put("b", "in/x.txt", b"hello")
    info = s.head("b", "in/x.txt")
    assert info.size == 5 and info.version_id is None
    assert s.move("b", "in/x.txt", "s3://b/archive/", sub_prefix="R/") == "archive/R/x.txt"
    assert s.exists("b", "archive/R/x.txt") and not s.exists("b", "in/x.txt")
    with pytest.raises(ValueError):
        s.put("b", "../../escape.txt", b"x")
    assert parse_uri("s3://bucket/a/b") == ("bucket", "a/b/")
    with pytest.raises(ValueError):
        parse_uri("ftp://x/y")


def test_spark_engine_requires_pyspark_and_jdbc_url(tmp_path):
    pytest.importorskip("pyspark", reason="pyspark not installed; Spark engine is exercised in a Spark/Glue environment")
    from framework.errors import ConfigError
    from framework.load.engine.spark_engine import SparkEngine

    with pytest.raises(ConfigError):
        SparkEngine(settings(), spark=object()).load_to_staging(
            None, file_path="x", cfg=cfg(), stg_columns=["a"], btch_id="b", load_id=1, src_file_nm="x", loaded_at=None)
