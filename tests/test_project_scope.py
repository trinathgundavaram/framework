"""Project-scoped steps and overlapping runs."""
import os
from datetime import date

import pytest

from framework import db as locks
from framework.adapters import LogChannel
from framework.common import ConfigError
from framework.modules import run_module

from .helpers import add_override, create_batches, file_name, make_app, put_file, q1, qa, seed_config, utc


def setup(conn, tmp_path, **kw):
    seed_config(conn, **kw)
    app, clock, rules = make_app(conn, tmp_path, utc(2026, 2, 1, 13, 0))
    create_batches(app)
    return app, clock, rules


def add_prjb_config(conn, location="s3://inbound/prja/in/"):
    """A PRJB source whose file config points at the given inbound folder."""
    with conn.transaction():
        conn.execute("""INSERT INTO ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt,
                          Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, S3_Src_File_Path,
                          Src_File_Archive_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm)
                        VALUES ('PRJB','tbl_x','S1','PRJB_TBLX_S1_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt','|',1,0,0,
                                %s,'s3://inbound/prjb/archive/','stg_t','tbl_x','core_t')""", (location,))


def events(conn, event_ty):
    return qa(conn, "SELECT * FROM CMS_ComplianceExceptionsAudit WHERE Event_Ty=%s", event_ty)


def test_project_sweep_in_its_own_folder_quarantines_unmatched_files_for_that_project(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    put_file(app, file_name("S1"), ["1|1|a"])
    put_file(app, "junk.txt", ["x"])
    out = run_module(app, "FILE_LOAD", {"project": "PRJA"})
    assert (out.exit_code, out.result.scanned, out.result.promoted, out.result.quarantined) == (0, 2, 1, 1)
    assert out.result.locations == ["inbound/prja/in/"]
    assert events(conn, "FILE_REJECTED_UNPARSEABLE")[0]["project_cd"] == "PRJA"


def test_project_sweep_in_a_shared_folder_leaves_other_projects_files(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    add_prjb_config(conn)
    put_file(app, file_name("S1"), ["1|1|a"])
    prjb = put_file(app, "PRJB_TBLX_S1_MONTHLY_20260101_20260131_20260201093000.txt", ["1|1|b"])
    put_file(app, "junk.txt", ["x"])
    s = run_module(app, "FILE_LOAD", {"project": "PRJA"}).result
    assert (s.scanned, s.promoted, s.skipped, s.quarantined) == (3, 1, 2, 0)
    assert app.store.exists("inbound", prjb) and app.store.exists("inbound", "prja/in/junk.txt")
    everything = run_module(app, "FILE_LOAD", {}).result
    assert everything.quarantined == 2 and not app.store.exists("inbound", "prja/in/junk.txt")


def test_project_sweep_rules(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    s = run_module(app, "FILE_LOAD", {"project": "NOPE"})
    assert s.exit_code == 1 and "no active file config" in s.result.errors[0]
    with pytest.raises(ConfigError, match="not both"):
        run_module(app, "FILE_LOAD", {"project": "PRJA", "bucket": "inbound", "prefix": "prja/in/"})


def test_an_object_being_loaded_elsewhere_is_skipped(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    key = put_file(app, file_name("S1"), ["1|1|a"])
    other = locks.connect(os.environ["TEST_DATABASE_URL"])
    try:
        assert locks.try_lock(other, locks.object_key("inbound", key, None))
        out = app.pipeline.process_file("inbound", key)
        assert (out.load_id, out.result) == (None, "IN_PROGRESS")
        assert q1(conn, "SELECT count(*) n FROM ComplianceFileLoad")["n"] == 0
    finally:
        other.close()
    assert app.pipeline.process_file("inbound", key).result == "PROMOTED"


def test_a_technical_failure_is_reported_once_per_load(conn, tmp_path):
    app, clock, rules = setup(conn, tmp_path)
    rules.error.add("FILE_LEVEL")
    put_file(app, file_name("S1"), ["1|1|a"])
    for _ in range(3):
        assert run_module(app, "FILE_LOAD", {"project": "PRJA"}).exit_code == 1
    ev = events(conn, "FILE_TECHNICAL_FAILURE")
    assert len(ev) == 1 and ev[0]["project_cd"] == "PRJA" and ev[0]["notified_ind"] == 0


def test_decisions_are_project_scoped_and_invalid_overrides_reported_once(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    oid = add_override(conn, q1(conn, "SELECT Req_ID FROM ComplianceRequestControl WHERE Src_ID='S1'")["req_id"],
                       "REUSE", date(2026, 3, 31))
    assert run_module(app, "OVERRIDE_DECISIONS", {"project": "OTHER"}).result.invalid == []
    for _ in range(2):
        out = run_module(app, "OVERRIDE_DECISIONS", {"project": "PRJA"})
        assert (out.exit_code, out.result.invalid) == (1, [oid])
    assert len(events(conn, "OVERRIDE_INVALID_DETECTED")) == 1


def test_batch_close_module_is_scoped_and_never_fails_on_waiting_batches(conn, tmp_path):
    app, clock, _ = setup(conn, tmp_path)
    put_file(app, file_name("S1"), ["1|1|a"])
    run_module(app, "FILE_LOAD", {"project": "PRJA"})
    clock.set(utc(2026, 2, 2, 12, 0))
    assert run_module(app, "BATCH_CLOSE", {"project": "OTHER"}).result.evaluated == 0
    out = run_module(app, "BATCH_CLOSE", {"project": "PRJA", "run_type": "MONTHLY"})
    assert (out.exit_code, len(out.result.closed), len(out.result.waiting)) == (0, 1, 1)


def test_notify_is_project_scoped_and_the_catch_all_sends_the_rest_once(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    add_prjb_config(conn, "s3://inbound/prjb/in/")
    put_file(app, "junk.txt", ["x"])
    put_file(app, "junk.txt", ["x"], key_prefix="prjb/in/")
    run_module(app, "FILE_LOAD", {"project": "PRJA"})
    run_module(app, "FILE_LOAD", {"project": "PRJB"})
    with conn.transaction():
        app.pipeline.logger.audit("CONFIG_VALIDATION_FAILED", description="x")
    ch = LogChannel()
    assert app.notifier(ch).run(project_cd="PRJA") == 1
    assert app.notifier(ch).run(project_cd="PRJA") == 0
    assert app.notifier(ch).run() == 2
    assert app.notifier(ch).run() == 0 and len(ch.sent) == 3


def test_notify_leaves_a_failed_send_for_the_next_run(conn, tmp_path):
    app, *_ = setup(conn, tmp_path)
    for name in ("junk1.txt", "junk2.txt"):
        put_file(app, name, ["x"])
    run_module(app, "FILE_LOAD", {"project": "PRJA"})

    class Flaky(LogChannel):
        def send(self, msg):
            if not self.sent and not getattr(self, "failed", False):
                self.failed = True
                raise RuntimeError("SES throttled")
            super().send(msg)

    ch = Flaky()
    first = app.notifier(ch)
    assert first.run(project_cd="PRJA") == 1 and len(first.failed) == 1
    second = app.notifier(ch)
    assert second.run(project_cd="PRJA") == 1 and second.failed == []
    assert q1(conn, "SELECT count(*) n FROM CMS_ComplianceExceptionsAudit WHERE Notified_Ind=0")["n"] == 0


def test_step_modules_accept_the_workflow_parameters(conn, tmp_path):
    """Each Step Functions step is `run --module <STEP> --project <P>` (+ run type / table where accepted)."""
    app, clock, _ = setup(conn, tmp_path)
    for module, params in (("BATCH_CREATION", {"project": "PRJA", "run_type": "MONTHLY", "period": "PREV_CALENDAR_MONTH"}),
                           ("FILE_LOAD", {"project": "PRJA"}), ("OVERRIDE_DECISIONS", {"project": "PRJA"}),
                           ("BATCH_CLOSE", {"project": "PRJA", "run_type": "MONTHLY", "table": "tbl_x"}),
                           ("NOTIFY", {"project": "PRJA"})):
        assert run_module(app, module, params).exit_code == 0, module


def test_notify_step_exits_1_when_an_email_fails(conn, tmp_path, monkeypatch):
    app, *_ = setup(conn, tmp_path)
    put_file(app, "junk1.txt", ["x"])
    run_module(app, "FILE_LOAD", {"project": "PRJA"})

    class Down(LogChannel):
        def send(self, msg):
            raise RuntimeError("SES down")

    real = app.notifier
    monkeypatch.setattr(app, "notifier", lambda channel=None: real(Down()))
    out = run_module(app, "NOTIFY", {"project": "PRJA"})
    assert out.exit_code == 1 and out.result == {"sent": 0, "failed": 1}
