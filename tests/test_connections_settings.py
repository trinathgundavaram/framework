import configparser
from datetime import date

import pytest

from framework.app import bootstrap
from framework.connections import ConnectionManager, ConnectionResolver
from framework.db import load_metadata_settings
from framework.errors import ConfigError
from framework.settings import Settings, coerce, read_config_file


def ini(text: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str.lower
    cp.read_string(text)
    return cp


FILE = ini("""
[settings]
object_store = local
lock_timeout_seconds = 60
supported_file_types = .TXT, .csv, .xlsx

[metadata_db]
dsn = host=filehost port=5433 dbname=filedb user=fileuser
sslmode = require
secret_name = meta-secret

[connection:dw_one]
host = dwhost
database = dwdb
password_env = DW_PW
""")


def secrets(name):
    return {"meta-secret": {"host": "sechost", "port": 6000, "dbname": "secdb", "username": "secuser",
                            "password": "secpw"},
            "row-secret": {"username": "rowsecuser", "password": "rowsecpw", "host": "rowsechost"}}[name]


def test_settings_precedence_env_file_metadata_default():
    s = Settings.load(env={"FRAMEWORK_LOCK_TIMEOUT_SECONDS": "5", "FRAMEWORK_METADATA_SCHEMA": "fw_x"}, config=FILE)
    assert (s.lock_timeout_seconds, s.sources["lock_timeout_seconds"]) == (5, "env")
    assert (s.object_store, s.sources["object_store"]) == ("local", "file")
    assert s.supported_file_types == [".txt", ".csv", ".xlsx"]
    assert s.metadata_schema == "fw_x"
    unknown = s.apply_metadata({"LOCK_TIMEOUT_SECONDS": "99", "OBJECT_STORE": "s3", "GO_LIVE_DATE": "2026-03-01",
                                "METADATA_SCHEMA": "ignored", "RULE_ENGINE": None, "MYSTERY": "1"})
    assert unknown == ["MYSTERY"]
    assert s.lock_timeout_seconds == 5 and s.object_store == "local"          # env / file win
    assert s.go_live_date == date(2026, 3, 1) and s.sources["go_live_date"] == "metadata"
    assert s.metadata_schema == "fw_x" and s.rule_engine == "gre"             # bootstrap / NULL ignored
    assert s.sources.get("rule_engine") is None


def test_settings_coercion_and_validation():
    assert coerce("empty_as_null", "No") is False
    assert coerce("gre_entrypoint", "") is None
    assert coerce("spark_jdbc_properties", '{"a": "1"}') == {"a": "1"}
    assert coerce("cycle_init_existing_batch", "fail") == "FAIL"
    with pytest.raises(ValueError):
        coerce("empty_as_null", "maybe")
    with pytest.raises(ValueError):
        Settings.load(env={"FRAMEWORK_METADATA_SCHEMA": "bad-name;drop"}, config=ini(""))


def test_config_file_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        read_config_file(str(tmp_path / "missing.ini"))
    assert read_config_file(None).sections() == []
    (tmp_path / "framework.ini").write_text("[settings]\nnotify_backend = aws\n")
    s = Settings.load(env={})
    assert s.config_file == "framework.ini" and s.notify_backend == "aws"
    p = tmp_path / "other.ini"
    p.write_text("[settings]\nnotify_backend = log\n")
    s = Settings.load(env={"FRAMEWORK_CONFIG_FILE": str(p)})
    assert s.config_file == str(p) and s.notify_backend == "log"


def test_metadata_connection_precedence():
    r = ConnectionResolver(Settings(metadata_schema="m1"), FILE, env={}, secret_loader=secrets)
    spec = r.metadata_spec()
    # file beats secret; secret fills the gaps (password)
    assert (spec.host, spec.port, spec.dbname, spec.user, spec.password, spec.sslmode) == \
        ("filehost", 5433, "filedb", "fileuser", "secpw", "require")
    assert spec.sources["password"] == "secret" and spec.sources["host"] == "file"
    assert "search_path=m1,public" in spec.conninfo()
    env = {"FRAMEWORK_DB_DSN": "postgresql://envuser@envhost:7000/envdb", "FRAMEWORK_DB_PORT": "7001",
           "FRAMEWORK_DB_PASSWORD_ENV": "MY_PW", "MY_PW": "envpw"}
    spec = ConnectionResolver(Settings(), FILE, env=env, secret_loader=secrets).metadata_spec()
    assert (spec.host, spec.port, spec.dbname, spec.user, spec.password, spec.sslmode) == \
        ("envhost", 7001, "envdb", "envuser", "envpw", "require")
    assert "envpw" not in repr(spec) and "envpw" not in spec.describe()


def test_metadata_connection_errors():
    with pytest.raises(ConfigError, match="no database name"):
        ConnectionResolver(Settings(), ini(""), env={}).metadata_spec()

    def broken(name):
        raise RuntimeError("denied")
    with pytest.raises(ConfigError, match="cannot read secret"):
        ConnectionResolver(Settings(), ini(""), env={"FRAMEWORK_DB_SECRET_NAME": "x"},
                           secret_loader=broken).metadata_spec()
    with pytest.raises(ConfigError, match="invalid DSN"):
        ConnectionResolver(Settings(), ini(""), env={"FRAMEWORK_DB_DSN": "host=a =b"}).metadata_spec()


def test_named_connection_precedence():
    row = {"connection_nm": "dw_one", "host": "rowhost", "port": 5555, "database_nm": "rowdb", "user_nm": "rowuser",
           "sslmode": "verify-full", "secret_nm": "row-secret", "password_env_var": None, "connect_timeout_sec": 9}
    r = ConnectionResolver(Settings(), FILE, env={"DW_PW": "filepw", "FRAMEWORK_CONN_DW_ONE_PORT": "6543"},
                           secret_loader=secrets)
    spec = r.named_spec("dw_one", row)
    assert (spec.host, spec.port, spec.dbname, spec.user, spec.password, spec.sslmode, spec.connect_timeout) == \
        ("dwhost", 6543, "dwdb", "rowuser", "filepw", "verify-full", 9)
    assert spec.sources == {"host": "file", "port": "env", "dbname": "file", "user": "metadata", "password": "file",
                            "sslmode": "metadata", "connect_timeout": "metadata"}
    assert spec.search_path is None
    assert spec.jdbc_url() == "jdbc:postgresql://dwhost:6543/dwdb?sslmode=verify-full"
    # row only, password from its secret
    spec = ConnectionResolver(Settings(), ini(""), env={}, secret_loader=secrets).named_spec("x", row)
    assert (spec.host, spec.user, spec.password) == ("rowhost", "rowuser", "rowsecpw")
    assert r.named_spec("metadata", None).name == "METADATA"
    assert r.has_external_definition("dw_one") and not r.has_external_definition("other")


def test_connection_manager_registry(conn):
    mgr = ConnectionManager(ConnectionResolver(Settings(), ini(""), env={}), metadata_conn=conn)
    assert mgr.get(None) is conn and mgr.get("METADATA") is conn and mgr.is_metadata("metadata")
    assert mgr.spec(None).dbname == conn.info.dbname
    with pytest.raises(ConfigError, match="not registered"):
        mgr.get("NOPE")
    with conn.transaction():
        conn.execute("INSERT INTO ComplianceDbConnection (Connection_Nm, Database_Nm, Host, Port, User_Nm, Active_Ind) "
                     "VALUES ('OFF', %s, %s, %s, %s, 0)",
                     (conn.info.dbname, conn.info.host, conn.info.port, conn.info.user))
        conn.execute("INSERT INTO ComplianceDbConnection (Connection_Nm, Database_Nm, Host, Port, User_Nm) "
                     "VALUES ('SAME', %s, %s, %s, %s)", (conn.info.dbname, conn.info.host, conn.info.port, conn.info.user))
    with pytest.raises(ConfigError, match="inactive"):
        mgr.spec("OFF")
    other = mgr.get("SAME")
    assert other is not conn and mgr.get("SAME") is other
    assert other.execute("SELECT 1 AS x").fetchone() == {"x": 1}
    assert mgr.spec("SAME").sources["dbname"] == "metadata"
    mgr.close()
    assert other.closed and conn.closed


def test_bootstrap_loads_metadata_settings(conn, monkeypatch, tmp_path):
    from .conftest import DSN, SCHEMA
    monkeypatch.chdir(tmp_path)
    with conn.transaction():
        conn.execute("UPDATE ComplianceFrameworkSetting SET Setting_Val='17' WHERE Setting_Nm='LOCK_TIMEOUT_SECONDS'")
        conn.execute("UPDATE ComplianceFrameworkSetting SET Setting_Val='aws' WHERE Setting_Nm='NOTIFY_BACKEND'")
    assert load_metadata_settings(conn)["LOCK_TIMEOUT_SECONDS"] == "17"
    env = {"FRAMEWORK_DB_DSN": DSN, "FRAMEWORK_METADATA_SCHEMA": SCHEMA, "FRAMEWORK_NOTIFY_BACKEND": "log"}
    settings, conns, unknown = bootstrap(env=env)
    try:
        assert settings.lock_timeout_seconds == 17 and settings.sources["lock_timeout_seconds"] == "metadata"
        assert settings.notify_backend == "log" and unknown == []
        assert conns.meta.execute("SHOW search_path").fetchone()["search_path"].startswith(SCHEMA)
    finally:
        conns.close()
    with conn.transaction():
        conn.execute("UPDATE ComplianceFrameworkSetting SET Setting_Val='soon' WHERE Setting_Nm='LOCK_TIMEOUT_SECONDS'")
    with pytest.raises(ConfigError, match="invalid value"):
        bootstrap(env=env)
    with pytest.raises(ConfigError, match="not initialised"):
        bootstrap(env=dict(env, FRAMEWORK_METADATA_SCHEMA="no_such_schema"))
