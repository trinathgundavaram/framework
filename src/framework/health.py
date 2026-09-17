"""Operational health queries (design §15.3)."""
from __future__ import annotations

from datetime import timedelta

import psycopg

from .clock import Clock
from .settings import Settings


def report(conn: psycopg.Connection, clock: Clock, settings: Settings) -> dict[str, list[dict]]:
    now = clock.now()
    stale = now - timedelta(minutes=settings.heartbeat_stale_minutes)
    return {
        "stale_loads": conn.execute(
            """SELECT Load_ID, S3_Key, Load_Stat, Heartbeat_Dtts FROM ComplianceFileLoad
                WHERE Load_Stat IN ('RECEIVED','STAGING','STAGED','RULES_RUNNING','FAILED_TECHNICAL')
                  AND COALESCE(Heartbeat_Dtts, Updated_Dtts) < %s ORDER BY Load_ID""", (stale,)).fetchall(),
        "pending_reviews": conn.execute(
            """SELECT Ovrd_ID, Override_Ty, Btch_ID, Candidate_Load_ID, Created_Dtts FROM ComplianceBatchOverride
                WHERE Apprvl_Stat='PENDING_REVIEW' ORDER BY Ovrd_ID""").fetchall(),
        "failed_promotions": conn.execute(
            "SELECT Ovrd_ID, Btch_ID FROM ComplianceBatchOverride WHERE Promotion_Stat='FAILED'").fetchall(),
        "triggers_in_flight": conn.execute(
            "SELECT Trigger_ID, Extract_ID, Requested_Dtts FROM ComplianceExtractTrigger WHERE Call_Stat='REQUESTED'"
        ).fetchall(),
        "extracts_past_hold_not_triggered": conn.execute(
            """SELECT Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Eligibility_Cd,
                      Eligibility_Rsn_Txt FROM ComplianceExtractControl
                WHERE Trigger_Stat IN ('NOT_TRIGGERED','FAILED') AND Earliest_Trigger_Dt < %s
                ORDER BY Extract_ID""", (now.date(),)).fetchall(),
        "retrigger_required": conn.execute(
            "SELECT Extract_ID, Eligibility_Cd FROM ComplianceExtractControl WHERE Retrigger_Required_Ind=1"
        ).fetchall(),
        "quarantine_by_reason": conn.execute(
            """SELECT Quarantine_Rsn_Cd, count(*) AS n FROM ComplianceFileLoad WHERE Load_Stat='QUARANTINED'
                GROUP BY Quarantine_Rsn_Cd ORDER BY n DESC""").fetchall(),
    }
