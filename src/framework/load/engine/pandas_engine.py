"""Single-process engine: Python reader + Postgres COPY, delete+load in one transaction.

Named PANDAS in config; pandas itself is only needed for xlsx/parquet files."""
from __future__ import annotations

import psycopg
from psycopg import sql

from ...errors import FileRejected
from ...ingest.file_reader import read_file, sanitize_db_error
from .base import ExecutionEngine, StageResult


class PandasEngine(ExecutionEngine):
    def load_to_staging(self, conn, *, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm, loaded_at,
                        target=None):
        result = read_file(file_path, cfg, len(stg_columns), self.settings)
        table = sql.Identifier(cfg.stg_schema_nm.lower(), cfg.stg_tblnm.lower())
        cols = [*stg_columns, "btch_id", "load_id", "src_file_nm", "stg_load_dtts"]
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(table, sql.SQL(", ").join(map(sql.Identifier, cols)))
        try:
            with conn.transaction():
                conn.execute(sql.SQL("DELETE FROM {} WHERE btch_id = %s").format(table), (btch_id,))
                with conn.cursor() as cur:
                    with cur.copy(copy_sql) as cp:
                        for rec in result.rows:
                            cp.write_row([*rec, btch_id, load_id, src_file_nm, loaded_at])
        except (psycopg.errors.DataError, psycopg.errors.IntegrityError) as e:
            raise FileRejected("FILE_PARSE_ERROR",
                               f"value does not fit staging column types: {sanitize_db_error(str(e.diag.message_primary))}")
        return StageResult(result.data_row_count, result.trailer_count)
