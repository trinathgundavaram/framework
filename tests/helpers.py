"""Shared test fixtures/builders. All names are synthetic."""
from __future__ import annotations

from datetime import date, datetime, timezone

from framework.adapters import LocalObjectStore, RuleOutcome
from framework.app import App
from framework.common import FixedClock
from framework.config import render
from framework.settings import Settings

TEMPLATE = "{PROJECT}_{TABLE}_{SRC}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt"
TZ = "America/Chicago"



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


def make_app(conn, tmp_path, now: datetime, **overrides) -> tuple[App, FixedClock, FakeRules]:
    """Job-level settings mirror what the project's scheduled jobs would pass."""
    settings = Settings(object_store="local", local_store_root=str(tmp_path / "store"), lock_timeout_seconds=2,
                        business_tz=TZ)
    for k, v in overrides.items():
        setattr(settings, k, v)
    clock = FixedClock(now)
    rules = FakeRules()
    app = App(conn, clock, settings, LocalObjectStore(settings.local_store_root), rules)
    return app, clock, rules


def seed_config(conn, *, sources=("S1", "S2"), sla=2, allow_zero=0, has_header=1, carry_fwd=0, rules=True,
                adhoc_sla=1):
    with conn.transaction():
        for s in sources:
            conn.execute("INSERT INTO ComplianceSourceSystem (Src_Cd, Src_Nm, Src_Ty) VALUES (%s,%s,'VENDOR')", (s, s))
        conn.execute("""INSERT INTO ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind)
                        VALUES ('MONTHLY','monthly','ROUTINE',%s,%s), ('ADHOC','ad hoc','ADHOC',%s,%s)""",
                     (sla, carry_fwd, adhoc_sla, carry_fwd))
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


def add_override(conn, req_id: int, override_ty: str, valid_thru, *, reuse_btch_id=None, approved=True,
                 who="approver") -> int:
    """What sql/approvals.sql does: one manual row, approved with a validity date."""
    with conn.transaction():
        return conn.execute(
            """INSERT INTO ComplianceBatchOverride
                 (Override_Ty, Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
                  Req_Dt_Key, Btch_ID, Reuse_Btch_ID, Rsn, Requested_By, Apprvl_Stat, Apprvd_By, Apprvd_Dtts,
                  Valid_Thru_Dt_Key)
               SELECT %s, Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
                      Req_Dt_Key, Btch_ID, %s, 'test', %s,
                      CASE WHEN %s THEN 'APPROVED' ELSE 'PENDING_REVIEW' END,
                      CASE WHEN %s THEN %s END, CASE WHEN %s THEN now() END,
                      CASE WHEN %s THEN %s::date END
                 FROM ComplianceRequestControl WHERE Req_ID=%s
               RETURNING Ovrd_ID""",
            (override_ty, reuse_btch_id, who, approved, approved, who, approved, approved, valid_thru,
             req_id)).fetchone()["ovrd_id"]


def stop_override(conn, ovrd_id: int, valid_thru) -> None:
    """Template 6: stop an approved override by moving its validity date into the past."""
    with conn.transaction():
        conn.execute("UPDATE ComplianceBatchOverride SET Valid_Thru_Dt_Key=%s, Updated_Dtts=now() WHERE Ovrd_ID=%s",
                     (valid_thru, ovrd_id))
