"""Test database fixtures.

Environment:
  TEST_DATABASE_URL        metadata database (required for DB tests)
  TEST_DATA_DATABASE_URL   optional separate data (staging/core) database; registered as connection DATA1
  TEST_METADATA_SCHEMA     metadata schema name (default cms_compliance)
"""
import os

import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from framework.db import connect, init_db

from . import helpers

DSN = os.environ.get("TEST_DATABASE_URL")
DATA_DSN = os.environ.get("TEST_DATA_DATABASE_URL")
SCHEMA = os.environ.get("TEST_METADATA_SCHEMA", "cms_compliance")
DATA_CONN_NM = "DATA1"


def _create_data_tables(c):
    for s in ("stg_t", "core_t"):
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(s)))
    c.execute("CREATE SCHEMA stg_t")
    c.execute("CREATE SCHEMA core_t")
    c.execute("""CREATE TABLE stg_t.tbl_x (id INT, amount NUMERIC(12,2), name TEXT,
                   btch_id VARCHAR(250) NOT NULL, load_id BIGINT NOT NULL,
                   src_file_nm VARCHAR(1024) NOT NULL, stg_load_dtts TIMESTAMPTZ NOT NULL)""")
    c.execute("""CREATE TABLE core_t.tbl_x (row_key BIGINT GENERATED ALWAYS AS IDENTITY, id INT NOT NULL,
                   amount NUMERIC(12,2), name TEXT, btch_id VARCHAR(250) NOT NULL, load_id BIGINT NOT NULL,
                   current_ind SMALLINT NOT NULL, load_dtts TIMESTAMPTZ NOT NULL, end_dtts TIMESTAMPTZ)""")


@pytest.fixture
def conn():
    """Metadata connection (search_path = metadata schema)."""
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set (see docs/framework-package.md - local testing)")
    c = connect(DSN, SCHEMA)
    for s in {SCHEMA, "cms_compliance", "stg_t", "core_t"}:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(s)))
    init_db(c, SCHEMA)
    data = None
    if DATA_DSN:
        data = connect(DATA_DSN)
        _create_data_tables(data)
        d = conninfo_to_dict(DATA_DSN)
        with c.transaction():
            c.execute("""INSERT INTO ComplianceDbConnection (Connection_Nm, Connection_Desc, Host, Port, Database_Nm,
                           User_Nm) VALUES (%s, 'test data database', %s, %s, %s, %s)""",
                      (DATA_CONN_NM, d.get("host"), int(d["port"]) if d.get("port") else None, d["dbname"],
                       d.get("user")))
        helpers.TARGET.update(name=DATA_CONN_NM, conn=data)
    else:
        _create_data_tables(c)
        helpers.TARGET.update(name=None, conn=c)
    try:
        yield c
    finally:
        helpers.TARGET.update(name=None, conn=None)
        if data is not None:
            data.close()
        c.close()


@pytest.fixture
def data(conn):
    """Connection holding the staging/core tables (same as `conn` unless TEST_DATA_DATABASE_URL is set)."""
    return helpers.TARGET["conn"]
