from ...settings import Settings
from .base import CallResult, ExtractConnector  # noqa: F401


def build_connector(job_ty: str, settings: Settings) -> ExtractConnector:
    if job_ty == "GLUE_JOB":
        from .glue_job import GlueJobConnector

        return GlueJobConnector(settings.aws_region)
    if job_ty == "HTTP_API":
        from .http_api import HttpApiConnector

        return HttpApiConnector(settings.aws_region, settings.http_accepted_status)
    raise ValueError(f"unknown Extract_Job_Ty {job_ty!r}")
