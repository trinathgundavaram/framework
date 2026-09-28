"""Shared test fixtures/builders. All names are synthetic."""
from __future__ import annotations

from datetime import date, datetime, timezone

from framework.adapters import LocalObjectStore, RuleOutcome
from framework.app import App
from framework.common import FixedClock
from framework.config import render
from framework.settings import Settings

TZ = "America/Chicago"


def template(src: str) -> str:
    """Each source's file config spells project, table and source out literally."""
    return f"PRJA_TBLX_{src}_{{RUNTY}}_{{RPTSTART}}_{{RPTEND}}_{{TS}}.txt"

def utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


class FakeRules:
    """Rule engine double: set `file_fail` / `period_fail` / `error` per scope."""

    def __init__(self):
        self.file_fail: dict[str, list[str]] = {}     # src_id -> failing rules
        self.period_fail: list[str] = []
        self.error: set[str] = set()                  # scopes that raise technical errors
        self.calls: list[dict] = []

    def run(self, conn, bindings, run_params, mode):
        self.calls.append(dict(run_params, mode=mode,
                               rules=[f"{b.gre_rule_group}:{b.gre_rule_variant}" for b in bindings]))
        scope = run_params["scope"]
        if scope in self.error:
            return RuleOutcome("ERROR", error="boom")
        failed = self.file_fail.get(run_params.get("src_id"), []) if scope == "FILE_LEVEL" else self.period_fail
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
        conn.execute("INSERT INTO ComplianceProject (Project_Cd, Project_Desc) VALUES ('PRJA','Project A'), "
                     "('PRJB','Project B')")
        for s in sources:
            conn.execute("INSERT INTO ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty) VALUES (%s,%s,'VENDOR')", (s, s))
        conn.execute("""INSERT INTO ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind)
                        VALUES ('MONTHLY','monthly','ROUTINE',%s,%s), ('ADHOC','ad hoc','ADHOC',%s,%s)""",
                     (sla, carry_fwd, adhoc_sla, carry_fwd))
        for s in sources:
            for rt in ("MONTHLY", "ADHOC"):
                conn.execute("""INSERT INTO ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty,
                                  Effective_Start_Dt_Key, Cmplnc_Vrsn) VALUES ('PRJA','tbl_x',%s,%s,'2025-01-01','V1')""",
                             (s, rt))
            conn.execute(
                """INSERT INTO ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd,
                     Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, S3_Src_File_Path,
                     Src_File_Archive_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Failr_Email_Notfn_Id)
                   VALUES ('PRJA','tbl_x',%s,%s,'|',%s,0,%s,'s3://inbound/prja/in/','s3://inbound/prja/archive/',
                           'stg_t','tbl_x','core_t','ops@example.com')""",
                (s, template(s), has_header, allow_zero))
            if rules:
                conn.execute("INSERT INTO ComplianceRuleBinding (Project_Cd, Table_Nm, Src_ID, Rule_Scope_Cd, "
                             "Gre_Rule_Group, Gre_Rule_Variant) VALUES ('PRJA','tbl_x',%s,'FILE_LEVEL','g_file',%s)",
                             (s, s))
        bind(conn, "PERIOD_LEVEL", "g_period", "all")


def bind(conn, scope, group, variant, *, project="PRJA", table="tbl_x", src="*", run_ty="*"):
    """One ComplianceRuleBinding row; '*' = all tables / sources / run types."""
    conn.execute("""INSERT INTO ComplianceRuleBinding (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rule_Scope_Cd,
                      Gre_Rule_Group, Gre_Rule_Variant) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                 (project, table, src, run_ty, scope, group, variant))


def create_batches(app, period="PREV_CALENDAR_MONTH", **kw):
    """What the routine half of the project's scheduled BATCH_CREATION job does."""
    return app.create_batches(project_cd="PRJA", run_ty="MONTHLY", period=period, **kw)


def file_name(src="S1", runty="MONTHLY", start=date(2026, 1, 1), end=date(2026, 1, 31),
              ts=datetime(2026, 2, 1, 9, 30, 0)) -> str:
    return render(template(src), runty=runty, rpt_start=start, rpt_end=end, ts=ts)


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
                 (Override_Ty, Req_ID, Btch_ID, Reuse_Btch_ID, Rsn_Txt, Created_By, Apprvl_Stat, Reviewed_By,
                  Reviewed_Dtts, Valid_Thru_Dt_Key)
               SELECT %s, Req_ID, Btch_ID, %s, 'test', %s,
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
