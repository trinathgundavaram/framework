"""Test database fixtures.

Environment:
  TEST_DATABASE_URL     PostgreSQL database for DB tests (DB tests are skipped when unset)
  TEST_METADATA_SCHEMA  metadata schema name (default cms_compliance)
"""
import os

import pytest
from psycopg import sql

from framework.db import connect, init_db

DSN = os.environ.get("TEST_DATABASE_URL")
SCHEMA = os.environ.get("TEST_METADATA_SCHEMA", "cms_compliance")


@pytest.fixture
def conn():
    """Framework connection (search_path = metadata schema); staging/core test tables in stg_t / core_t."""
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set (see docs/framework-package.md - local testing)")
    c = connect(DSN, SCHEMA)
    for s in {SCHEMA, "cms_compliance", "stg_t", "core_t"}:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(s)))
    init_db(c, SCHEMA)
    c.execute("CREATE SCHEMA stg_t")
    c.execute("CREATE SCHEMA core_t")
    c.execute("""CREATE TABLE stg_t.tbl_x (id INT, amount NUMERIC(12,2), name TEXT,
                   btch_id VARCHAR(250) NOT NULL, load_id BIGINT NOT NULL,
                   src_file_nm VARCHAR(1024) NOT NULL, stg_load_dtts TIMESTAMPTZ NOT NULL)""")
    c.execute("""CREATE TABLE core_t.tbl_x (row_key BIGINT GENERATED ALWAYS AS IDENTITY, id INT NOT NULL,
                   amount NUMERIC(12,2), name TEXT, btch_id VARCHAR(250) NOT NULL, load_id BIGINT NOT NULL,
                   current_ind SMALLINT NOT NULL, load_dtts TIMESTAMPTZ NOT NULL, end_dtts TIMESTAMPTZ)""")
    try:
        yield c
    finally:
        c.close()
