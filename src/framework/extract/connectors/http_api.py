from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Optional

from .base import CallResult, ExtractConnector


def _accepted(code: int, ranges: list[str]) -> bool:
    for r in ranges:
        lo, _, hi = r.partition("-")
        if lo and int(lo) <= code <= int(hi or lo):
            return True
    return False


class HttpApiConnector(ExtractConnector):
    """POST/PUT a JSON object {param_name: value} to the configured endpoint.

    Authentication is open question Q-08. Provisional support: if Auth_Secret_Nm is set, the secret
    (Secrets Manager, JSON) must contain {"header_name": ..., "header_value": ...}."""

    def __init__(self, region: str, accepted_status: list[str], secret_loader=None):
        self.region = region
        self.accepted_status = accepted_status
        self.secret_loader = secret_loader or self._load_secret

    def _load_secret(self, name: str) -> dict:
        import boto3

        raw = boto3.client("secretsmanager", region_name=self.region).get_secret_value(SecretId=name)
        return json.loads(raw["SecretString"])

    def call(self, policy, params):
        body = json.dumps({k: v for k, v in params}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if policy.auth_secret_nm:
            sec = self.secret_loader(policy.auth_secret_nm)
            headers[sec["header_name"]] = sec["header_value"]
        req = urllib.request.Request(policy.endpoint_url, data=body, headers=headers, method=policy.http_method)
        try:
            with urllib.request.urlopen(req, timeout=policy.call_timeout_sec) as resp:
                text = resp.read(2000).decode("utf-8", "replace")
                ref = resp.headers.get("x-request-id") or _ref_from(text)
                return CallResult(_accepted(resp.status, self.accepted_status), ref, text[:1000])
        except urllib.error.HTTPError as e:
            return CallResult(_accepted(e.code, self.accepted_status), None, f"HTTP {e.code}")
        except urllib.error.URLError as e:
            # connection refused / DNS failure -> nothing was sent; timeouts -> unknown
            ambiguous = isinstance(e.reason, (socket.timeout, TimeoutError))
            return CallResult(False, None, f"URLError: {e.reason}"[:1000], ambiguous=ambiguous)
        except (socket.timeout, TimeoutError) as e:
            return CallResult(False, None, f"timeout: {e}", ambiguous=True)


def _ref_from(text: str) -> Optional[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        for k in ("job_run_id", "jobRunId", "id", "request_id", "requestId"):
            if k in data:
                return str(data[k])
    return None
