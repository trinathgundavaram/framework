from __future__ import annotations

from .base import CallResult, ExtractConnector


class GlueJobConnector(ExtractConnector):
    """Starts the configured Glue job; parameters become job Arguments (names should include '--')."""

    def __init__(self, region: str, client=None):
        self._client = client
        self.region = region

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("glue", region_name=self.region)
        return self._client

    def call(self, policy, params):
        from botocore.exceptions import ClientError, EndpointConnectionError, ParamValidationError

        try:
            resp = self.client.start_job_run(JobName=policy.job_nm, Arguments={k: v for k, v in params})
        except (ClientError, ParamValidationError, EndpointConnectionError) as e:
            # the request was refused or never sent -> safe to report as not accepted
            return CallResult(False, response_txt=f"{type(e).__name__}: {e}"[:1000])
        except Exception as e:  # noqa: BLE001 - e.g. read timeout after send: outcome unknown
            return CallResult(False, response_txt=f"{type(e).__name__}: {e}"[:1000], ambiguous=True)
        return CallResult(True, job_run_ref=resp.get("JobRunId"), response_txt="started")
