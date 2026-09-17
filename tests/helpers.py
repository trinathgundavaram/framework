"""Shared test fixtures/builders. All names are synthetic."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from framework.adapters import CallResult, LocalObjectStore, RuleOutcome
from framework.app import App
from framework.common import FixedClock
from framework.config import render
from framework.settings import Settings

TEMPLATE = "{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt"
TZ = "America/Chicago"
EXTRACT_PARAMS = {"--PERIOD_START": "{rpt_start_dt_key}", "--BATCHES": "{btch_id_list}",
                  "--TRIGGER_ID": "{trigger_id}", "--MODE": "full"}


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


class FakeRules:
    """Rule engine double: set `file_fail` / `period_fail` / `error` per scope."""

    def __init__(self):
        self.file_fail: dict[str, list[str]] = {}     # src_cd -> failing rules
        self.period_fail: list[str] = []
        self.error: set[str] = set()                  # scopes that raise technical errors
        self.calls: list[dict] = []

    def run(self, conn, bindings, run_params, mode):
        self.calls.append(dict(run_params, mode=mode))
        scope = run_params["scope"]
        if scope in self.error:
            return RuleOutcome("ERROR", error="boom")
        failed = self.file_fail.get(run_params.get("src_cd"), []) if scope == "FILE_LEVEL" else self.period_fail
        if not failed:
            return RuleOutcome("PASSED")
        if mode == "GATE":
            return RuleOutcome("FAILED", failed_rules=list(failed))
        return RuleOutcome("PASSED_WITH_WARNINGS", warned_rules=list(failed))


@dataclass
class FakeConnector:
    mode: str = "accept"          # accept | reject | ambiguous | reject_then_accept
    calls: list = field(default_factory=list)

    def call(self, settings, params):
        self.calls.append(list(params))
        if self.mode == "accept" or (self.mode == "reject_then_accept" and len(self.calls) > 1):
            return CallResult(True, job_run_ref=f"jr_{len(self.calls)}", response_txt="ok")
        if self.mode == "ambiguous":
            return CallResult(False, response_txt="timeout", ambiguous=True)
        return CallResult(False, response_txt="HTTP 500")


def make_app(conn, tmp_path, now: datetime, **overrides) -> tuple[App, FixedClock, FakeRules, FakeConnector]:
    """Job-level settings mirror what the project's scheduled jobs would pass."""
    settings = Settings(object_store="local", local_store_root=str(tmp_path / "store"), lock_timeout_seconds=2,
                        call_retry_backoff_seconds=0, business_tz=TZ, extract_job_name="extract_job",
                        extract_max_call_retries=1, extract_params=dict(EXTRACT_PARAMS))
    for k, v in overrides.items():
        setattr(settings, k, v)
    clock = FixedClock(now)
    rules = FakeRules()
    connector = FakeConnector()
    app = App(conn, clock, settings, LocalObjectStore(settings.local_store_root), rules,
              connector_factory=lambda s: connector, sleep=lambda s: None)
    return app, clock, rules, connector


def seed_config(conn, *, sources=("S1", "S2"), sla=2, allow_zero=0, has_header=1, carry_fwd=0, rules=True):
    with conn.transaction():
        for s in sources:
            conn.execute("INSERT INTO ComplianceSourceSystem (Src_Cd, Src_Nm, Src_Ty) VALUES (%s,%s,'VENDOR')", (s, s))
        conn.execute("""INSERT INTO ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind)
                        VALUES ('MONTHLY','monthly','ROUTINE',%s,%s), ('ADHOC','ad hoc','ADHOC',1,0)""", (sla, carry_fwd))
        for s in sources:
            for rt in ("MONTHLY", "ADHOC"):
                conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_Cd, Run_Ty,
                                  Effective_Start_Dt, Cmplnc_Vrsn) VALUES ('PRJA','tbl_x',%s,%s,'2025-01-01','V1')""",
                             (s, rt))
            conn.execute(
                """INSERT INTO ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_Cd, Src_File_Nm_Tmplt, Project_Alias,
                     Table_Alias, Src_Alias, Src_File_Ty, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind,
                     Allow_Zero_Rcd_Ind, S3_Src_File_Path, Src_File_Archive_Path, S3_Quarantine_Path, Stg_Schema_Nm,
                     Stg_Tblnm, Core_Schema_Nm, Core_Tblnm, Failr_Email_Notfn_Id)
                   VALUES ('PRJA','tbl_x',%s,%s,'PRJA','TBLX',%s,'.txt','|',%s,0,%s,
                           's3://inbound/prja/in/','s3://inbound/prja/archive/','s3://inbound/prja/quarantine/',
                           'stg_t','tbl_x','core_t','tbl_x','ops@example.com')""",
                (s, TEMPLATE, s, has_header, allow_zero))
            if rules:
                conn.execute("INSERT INTO ComplianceRuleBinding VALUES ('PRJA','tbl_x',%s,'FILE_LEVEL','g_file',%s,1)",
                             (s, s))
        conn.execute("INSERT INTO ComplianceRuleBinding VALUES ('PRJA','tbl_x','*','PERIOD_LEVEL','g_period','all',1)")


def create_batches(app, period="PREV_CALENDAR_MONTH", **kw):
    """What the project's scheduled create-batches job does."""
    return app.create_batches(project_cd="PRJA", run_ty="MONTHLY", period=period, **kw)


def file_name(src="S1", runty="MONTHLY", start=date(2026, 1, 1), end=date(2026, 1, 31),
              ts=datetime(2026, 2, 1, 9, 30, 0)) -> str:
    return render(TEMPLATE, project="PRJA", table="TBLX", src=src, runty=runty, rpt_start=start, rpt_end=end, ts=ts)


def put_file(app, name: str, rows: list[str], header: bool = True, key_prefix="prja/in/") -> str:
    body = ("id|amount|name\n" if header else "") + "".join(r + "\n" for r in rows)
    key = key_prefix + name
    app.store.put("inbound", key, body.encode())
    return key


def q1(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def qa(conn, sql, *params):
    return conn.execute(sql, params).fetchall()


def approve_reopen(conn, ovrd_id: int, reviewed_load_id: int, who="approver") -> int:
    with conn.transaction():
        return conn.execute(
            """UPDATE ComplianceBatchOverride
                  SET Apprvl_Stat='APPROVED', Apprvd_By=%s, Apprvd_Dtts=now(), Reviewed_Load_ID=%s,
                      History = History || E'\\n' || 'APPROVED', Updated_Dtts=now()
                WHERE Ovrd_ID=%s AND Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
                  AND Apprvl_Stat='PENDING_REVIEW' AND Candidate_Load_ID=%s""",
            (who, reviewed_load_id, ovrd_id, reviewed_load_id)).rowcount
