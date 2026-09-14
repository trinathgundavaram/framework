# Reuse, late arrival, correction, and batch close — concept guide

This is the plain-language + DDL reference for how `ComplianceRequestControl`,
`ComplianceBatchOverride`, and `ComplianceExtractControl` work together. Two
compact worked examples cover every scenario discussed: a carry-forward
table (5 days, 1 FDR) and a non-carry-forward table (5 days, 3 FDRs).

## The three tables, in one sentence each

- **`ComplianceRequestControl` (CRC)** — current-state, **one row per
  (source, table, date), forever**. Says what happened *for that source, on
  that date*. Updated in place; never versioned, never duplicated.
- **`ComplianceBatchOverride` (Override)** — sparse, current-state, **one
  row per thing that needs a human decision before data can be reused or a
  closed date can be touched again**. Auto-created, manually resolved.
- **`ComplianceExtractControl`** — current-state, **one row per
  (table, date)**, no source column. Tracks whether that date's CMS
  submission is complete and whether it's locked.

## The four concepts

### 1. Carry-forward reuse
Some sources are allowed to let one approved file stand in for several
days' worth of data (`Carry_Fwd_Elig_Ind = Y` on the crosswalk table).
The **first day a new file lands, an Override row is auto-created**
(`PENDING_REVIEW`) — future days are **blocked from reusing it** until a
human approves. Approval unlocks reuse; revocation cuts it off going
forward (past days already carried are not rewritten).

### 2. Batch close
Waiting indefinitely for a file that may never come isn't viable for daily
processing. So **close is schedule-driven, not completion-driven**: at each
date's SLA cutoff, its CRC row(s) and extract row close automatically —
`PARTIAL` closes exactly like `COMPLETE`. Closing means "nothing may write
to this row again except through an approved Override."

### 3. Late arrival / correction reopen
A file landing **after** its target CRC row is closed — whether that
source was previously `MISSING` (late arrival) or had a file that turned
out to be wrong (correction) — cannot update CRC directly. It creates an
Override row instead (`Override_Ty` distinguishes the two cases) and waits
for approval. Approval reopens the CRC row, updates it, and triggers the
extract to regenerate and re-close.

### 4. Rejecting files that were never expected
A file for an unrecognized source, an unscheduled date, or an exact repeat
of one already on file must **never** reach the Override queue — that
queue's value depends on every row in it genuinely needing a judgment
call. These are rejected upstream, quarantined, and only logged to the
audit table.

## DDLs

```sql
CREATE TABLE ComplianceSourceSystem (
    Src_Cd        VARCHAR(50)  PRIMARY KEY,
    Src_Nm        VARCHAR(100) NOT NULL,
    Src_Ty        VARCHAR(20)  NOT NULL,        -- VENDOR / INTERNAL
    Active_Ind    SMALLINT     NOT NULL DEFAULT 1
);

CREATE TABLE ComplianceRunType (
    Run_Ty        VARCHAR(20)  PRIMARY KEY,
    Run_Ty_Desc   VARCHAR(200),
    SLA_Days      INT
);

CREATE TABLE ComplianceDataSetSourceXwalk (
    Project_Cd            VARCHAR(50) NOT NULL,
    Table_Nm              VARCHAR(50) NOT NULL,
    Src_Cd                VARCHAR(50) NOT NULL REFERENCES ComplianceSourceSystem(Src_Cd),
    Run_Ty                VARCHAR(20) NOT NULL REFERENCES ComplianceRunType(Run_Ty),
    Cmplnc_Vrsn            VARCHAR(10) NOT NULL,
    Carry_Fwd_Elig_Ind     SMALLINT NOT NULL DEFAULT 0,
    File_Naming_Pattern    VARCHAR(255),
    Rules_Vld_Md           VARCHAR(10) NOT NULL DEFAULT 'GATE',
    Active_Ind             SMALLINT NOT NULL DEFAULT 1,
    PRIMARY KEY (Project_Cd, Table_Nm, Src_Cd, Run_Ty)
);

CREATE TABLE ComplianceRequestControl (
    Req_ID              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    Project_Cd           VARCHAR(50)  NOT NULL,
    Table_Nm             VARCHAR(50)  NOT NULL,
    Src_Cd               VARCHAR(50)  NOT NULL,
    Run_Ty               VARCHAR(20)  NOT NULL,
    Req_Dt_Key           DATE         NOT NULL,
    Btch_ID              VARCHAR(120) NOT NULL,
    Cmplnc_Vrsn           VARCHAR(10),
    Rpt_Start_Dt_Key      DATE,
    Rpt_End_Dt_Key        DATE,
    Req_Stat             VARCHAR(30)  NOT NULL,
    File_Received_Ind     SMALLINT     NOT NULL DEFAULT 0,
    Resolution_Ty         VARCHAR(20)  NOT NULL,   -- NEW_FILE / CARRIED_FORWARD / MISSING
    Used_Btch_ID          VARCHAR(120),
    Late_Arrival_Ind       SMALLINT     NOT NULL DEFAULT 0,
    Batch_Close_Ind        SMALLINT     NOT NULL DEFAULT 0,
    Batch_Closed_Dtts       TIMESTAMP,
    Batch_Closed_By         VARCHAR(100),
    Created_Dtts          TIMESTAMP    NOT NULL DEFAULT now(),
    Updated_Dtts          TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (Project_Cd, Table_Nm, Src_Cd, Req_Dt_Key)
);

CREATE TABLE ComplianceBatchOverride (
    Ovrd_ID              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    Req_ID                BIGINT       NOT NULL REFERENCES ComplianceRequestControl(Req_ID),
    Btch_ID               VARCHAR(120) NOT NULL,
    Used_Btch_ID           VARCHAR(120),
    Override_Ty            VARCHAR(30)  NOT NULL,  -- CARRY_FORWARD_REUSE / LATE_ARRIVAL_REOPEN / CORRECTION_REOPEN
    Prior_Btch_ID           VARCHAR(120),           -- snapshot of CRC's Btch_ID before this override, if any
    Prior_Resolution_Ty      VARCHAR(20),           -- snapshot of CRC's Resolution_Ty before this override
    Apprvl_Stat            VARCHAR(20)  NOT NULL,  -- PENDING_REVIEW / APPROVED / REVOKED
    Apprvd_By               VARCHAR(100),
    Apprvd_Dtts             TIMESTAMP,
    Revoked_By              VARCHAR(100),
    Revoked_Dtts             TIMESTAMP,
    Revocation_Rsn           TEXT,
    History                VARCHAR(2000) NOT NULL,
    UNIQUE (Req_ID)
);

CREATE TABLE ComplianceExtractControl (
    Extract_ID            BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    Project_Cd             VARCHAR(50)  NOT NULL,
    Table_Nm               VARCHAR(50)  NOT NULL,
    Req_Dt_Key             DATE         NOT NULL,
    Extract_Stat           VARCHAR(20)  NOT NULL,  -- PARTIAL / COMPLETE
    Included_Src_Cds        VARCHAR(500),           -- comma-separated or JSON array
    Missing_Src_Cds          VARCHAR(500),
    Extract_Generated_Dtts   TIMESTAMP,
    Extract_Ref             VARCHAR(500),
    Extract_Close_Ind        SMALLINT     NOT NULL DEFAULT 0,
    Extract_Closed_Dtts       TIMESTAMP,
    Extract_Closed_By         VARCHAR(100),
    Reopen_Ind              SMALLINT     NOT NULL DEFAULT 0,
    Reopened_By              VARCHAR(100),
    Reopened_Dtts             TIMESTAMP,
    Reopen_Rsn               TEXT,
    Created_Dtts            TIMESTAMP    NOT NULL DEFAULT now(),
    Updated_Dtts            TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (Project_Cd, Table_Nm, Req_Dt_Key)
);

CREATE TABLE CMS_ComplianceExceptionsAudit (
    Event_ID       BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    Btch_ID        VARCHAR(120),
    Event_Ctgy     VARCHAR(20)  NOT NULL,   -- EXCEPTION / AUDIT
    Event_Ty       VARCHAR(50)  NOT NULL,
    Sevrty         VARCHAR(10),
    Actor          VARCHAR(100),
    Event_Dtts     TIMESTAMP    NOT NULL DEFAULT now(),
    Description    TEXT
);
```

## Worked example A — carry-forward table (PROGAUDIT / ODAG1, FDR 2001, Jan 1–5)

| Day | CRC row (`Req_Dt_Key`) | Override row action | Why |
|---|---|---|---|
| 1 | `NEW_FILE`, `Used_Btch_ID=self`. Closes end of day. | **Auto-created**: `Override_Ty=CARRY_FORWARD_REUSE`, `PENDING_REVIEW` | New file, source is carry-forward eligible |
| 2 | `MISSING`, `Used_Btch_ID=NULL`. Closes end of day. | Analyst approves later that day: `APPROVED`, `Apprvd_By=jsmith` | Day-2 run checked the anchor — still `PENDING_REVIEW` at run time, so reuse was **blocked** |
| 3 | `CARRIED_FORWARD`, `Used_Btch_ID=Day1`. Closes end of day. | No change | Anchor now `APPROVED` — reuse allowed |
| 4 | `CARRIED_FORWARD`, `Used_Btch_ID=Day1`. Closes end of day. | No change | Still approved |
| 5 | `MISSING`, `Used_Btch_ID=NULL`. Closes end of day. | Analyst revokes before the run: `REVOKED`, `Revoked_By=mgarcia` | Revocation cuts off *future* reuse — Days 3–4 (already closed) are untouched |

**Result: 5 CRC rows (one per day, never versioned), 1 Override row** whose
`History` reads: *"Day 1: row created, PENDING_REVIEW | Day 2: UPDATE →
APPROVED by jsmith | Day 5: UPDATE → REVOKED by mgarcia."*

## Worked example B — non-carry-forward table (PARTCODR / ROPENS, 3 FDRs, Jan 5)

Single business date, `Carry_Fwd_Elig_Ind=N`, so Override is only ever used
for reopening — never for reuse.

| Date | Event | What's written |
|---|---|---|
| Jan 6 (SLA cutoff) | 1001 on time, correct. 1002 no file. 1003 on time, **data later found wrong**. | CRC: 3 rows, all `Batch_Close_Ind=Y`. `ComplianceExtractControl`: `PARTIAL`, `Included=[1001,1003]`, `Missing=[1002]`, closed |
| Jan 7 | Duplicate resend of 1001's exact file | **Rejected upstream** — checksum matches what's on record. Quarantined. Audit: `DUPLICATE_FILE_IGNORED`. No CRC/Override write |
| Jan 7 | File for an unrecognized source (`9999`) | **Rejected upstream** — not in the crosswalk. Quarantined. Audit: `UNRECOGNIZED_SOURCE`. No CRC/Override write |
| Jan 8 | Corrected file for 1003 arrives | Target CRC row is closed → **Override auto-created**: `Override_Ty=CORRECTION_REOPEN`, `Prior_Btch_ID=<Jan6 batch>`, `Prior_Resolution_Ty=NEW_FILE`, `PENDING_REVIEW`. Analyst approves same day → CRC 1003 row updated in place (new `Btch_ID`, `Batch_Close_Ind` resets then recloses). Extract regenerates: still `PARTIAL` (1002 still missing) |
| Jan 9 | Late file for 1002 arrives (3 days late) | Target CRC row is closed and was `MISSING` → **Override auto-created**: `Override_Ty=LATE_ARRIVAL_REOPEN`, `Prior_Btch_ID=NULL`, `Prior_Resolution_Ty=MISSING`, `PENDING_REVIEW`. Analyst approves → CRC 1002 row updated (`NEW_FILE`, `Late_Arrival_Ind=Y`), recloses. Extract regenerates: **`COMPLETE`**, `Included=[1001,1002,1003]`, `Missing=[]`, recloses |

**Result: 3 CRC rows total** (one per FDR, each updated in place at most
once more after its initial close), **2 Override rows** (1003's correction,
1002's late arrival — `Override_Ty` tells them apart), **1 Extract row**
that reopened/reclosed twice, and **3 audit-only rejected-file events** that
never touched either core table.

## The line that matters

`Prior_Resolution_Ty` on the Override row is what lets you tell, after the
fact and without touching CRC's history (it has none — it's current-state
only), whether a given reopen was *filling a gap* (`MISSING`) or
*fixing a mistake* (`NEW_FILE`) — while the three upstream rejection checks
(unrecognized source, unscheduled date, duplicate checksum) make sure
neither the Override queue nor CRC ever sees a file that was never actually
expected.
