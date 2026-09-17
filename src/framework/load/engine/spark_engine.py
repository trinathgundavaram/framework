"""Spark engine for large files (D-62). Same contract as PandasEngine.

The staging delete runs through psycopg; the append goes through Spark JDBC. A partial append is
never promoted because promotion filters on Load_ID and checks the staged row count (§10.1).
Requires `pyspark` and FRAMEWORK_SPARK_JDBC_URL (+ FRAMEWORK_SPARK_JDBC_PROPERTIES)."""
from __future__ import annotations

from psycopg import sql

from ...errors import ConfigError, FileRejected
from ...ingest.file_reader import delimiter_for, scan_delimited
from .base import ExecutionEngine, StageResult


class SparkEngine(ExecutionEngine):
    def __init__(self, settings, spark=None):
        super().__init__(settings)
        self._spark = spark

    @property
    def spark(self):
        if self._spark is None:
            from pyspark.sql import SparkSession  # optional dependency

            self._spark = SparkSession.builder.appName("cms-compliance-framework").getOrCreate()
        return self._spark

    def load_to_staging(self, conn, *, file_path, cfg, stg_columns, btch_id, load_id, src_file_nm, loaded_at):
        from pyspark.sql import functions as F
        from pyspark.sql.types import StringType, StructField, StructType

        if not self.settings.spark_jdbc_url:
            raise ConfigError("FRAMEWORK_SPARK_JDBC_URL is required for Engine_Cd=SPARK")
        if cfg.src_file_ty not in (".txt", ".csv"):
            raise FileRejected("FILE_TYPE_NOT_SUPPORTED", f"Spark engine reads delimited files only, got {cfg.src_file_ty}")
        if cfg.has_trailer:
            raise FileRejected("FILE_TYPE_NOT_SUPPORTED", "Spark engine does not support trailer records yet (Q-02)")
        expected = scan_delimited(file_path, cfg, len(stg_columns), self.settings)  # C12/C13, streaming
        schema = StructType([StructField(c, StringType(), True) for c in stg_columns])
        df = (self.spark.read.option("header", str(cfg.has_header).lower())
              .option("sep", delimiter_for(cfg)).option("quote", self.settings.quote_char)
              .option("encoding", self.settings.file_encoding).option("mode", "FAILFAST")
              .schema(schema).csv(file_path))
        if self.settings.empty_as_null:
            df = df.select([F.when(F.col(c) == "", None).otherwise(F.col(c)).alias(c) for c in stg_columns])
        df = (df.withColumn("btch_id", F.lit(btch_id)).withColumn("load_id", F.lit(load_id))
              .withColumn("src_file_nm", F.lit(src_file_nm)).withColumn("stg_load_dtts", F.lit(loaded_at)))
        rows = expected
        table = sql.Identifier(cfg.stg_schema_nm.lower(), cfg.stg_tblnm.lower())
        with conn.transaction():
            conn.execute(sql.SQL("DELETE FROM {} WHERE btch_id = %s").format(table), (btch_id,))
        props = {"driver": "org.postgresql.Driver", "stringtype": "unspecified",
                 **self.settings.spark_jdbc_properties}
        try:
            (df.repartition(self.settings.spark_write_partitions).write
             .option("batchsize", self.settings.spark_batch_size)
             .jdbc(self.settings.spark_jdbc_url, f"{cfg.stg_schema_nm}.{cfg.stg_tblnm}", mode="append",
                   properties=props))
        except Exception as e:  # noqa: BLE001
            if "invalid input syntax" in str(e) or "out of range" in str(e):
                raise FileRejected("FILE_PARSE_ERROR", "value does not fit staging column types")
            raise
        return StageResult(rows, None)
