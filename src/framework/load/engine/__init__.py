from ...settings import Settings
from .base import ExecutionEngine, StageResult  # noqa: F401


def build_engine(engine_cd: str, settings: Settings) -> ExecutionEngine:
    if engine_cd == "PANDAS":
        from .pandas_engine import PandasEngine

        return PandasEngine(settings)
    if engine_cd == "SPARK":
        from .spark_engine import SparkEngine

        return SparkEngine(settings)
    raise ValueError(f"unknown Engine_Cd {engine_cd!r}")
