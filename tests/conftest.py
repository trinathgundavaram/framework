import os

import pytest

from framework.db import connect, init_db

DSN = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def conn():
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set (docker compose up -d; see docker-compose.yml)")
    c = connect(DSN)
    c.execute("DROP SCHEMA IF EXISTS cms_compliance CASCADE")
    c.execute("DROP SCHEMA IF EXISTS stg_t CASCADE")
    c.execute("DROP SCHEMA IF EXISTS core_t CASCADE")
    init_db(c)
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
