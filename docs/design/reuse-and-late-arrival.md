# Reuse, late arrival, correction, and batch close — concept guide

This is the plain-language + DDL reference for how `ComplianceRequestControl`,
`ComplianceBatchOverride`, and `ComplianceExtractControl` work together. Two
compact worked examples cover every scenario discussed: a carry-forward
table under `Run_Ty=CMS` (6 days, 1 FDR, including an anchor switch) and a
non-carry-forward table under `Run_Ty=DAILY` (5 days, 3 FDRs).

## The three tables, in one sentence each

- **`ComplianceRequestControl` (CRC)** — current-state, **one row per
  (source, table, run type, date), forever**. Says what happened *for that
  source, under that run type, on that date*. Updated in place; never
  versioned, never duplicated. Carries the reporting period it belongs to
  (`Rpt_Start_Dt_Key` / `Rpt_End_Dt_Key`) — the window a given `Run_Ty` is
  producing data for, which may be a single day or a fixed multi-week
  period, independent of how often `Req_Dt_Key` itself ticks forward.
- **`ComplianceBatchOverride` (Override)** — sparse, current-state, **one
  row per batch that needs a human decision before data can be reused or a
  closed date can be touched again**. Auto-created on every complete file
  load for a carry-forward-eligible source (not just the first ever one),
  manually resolved, and automatically kept to **at most one active
  (`APPROVED`) row per (project, table, source, run type, reporting
  period)**.
- **`ComplianceExtractControl`** — current-state, **one row per
  (table, date)**, no source column. Tracks whether that date's CMS
  submission is complete and whether it's locked.

## The four concepts

### 1. Carry-forward reuse
Some sources are allowed to let one approved file stand in for several
days' worth of data (`Carry_Fwd_Elig_Ind = Y` on the crosswalk table).
Every daily run resolves a source's batch in a strict priority order:

1. **Did a file arrive today?** → use it. `Resolution_Ty=NEW_FILE`,
   `Used_Btch_ID=self` — this always wins, regardless of whether an
   already-`APPROVED` anchor exists elsewhere in the grouping. A file
   that physically showed up today is never set aside in favor of an
   older one.
2. **No file today — is there a currently-`APPROVED` anchor in this
   grouping?** → carry it forward, `Used_Btch_ID` = that anchor's batch.
3. **Neither** → `MISSING`.

**Every time step 1 fires for such a source** — not only the first file
ever — an Override row is auto-created (`PENDING_REVIEW`), because the
pipeline can't know on its own whether *this* file should become the new
anchor; that's a judgment call. Until a human approves it, it plays no
part in *other* days' step-2 checks (though it still governs its own
day's extract via step 1, unconditionally). If a *different* anchor for
the same (project, table, source, run type, reporting period) is later
approved, the previously approved one is **automatically flipped to
`SUPERSEDED`** as part of that same approval — see §5 below for exactly
how that's enforced.

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

### 5. How Override entries actually get created, and how they're governed

**Creation is automatic, on every complete load — not just the first.**
Any time a source flagged `Carry_Fwd_Elig_Ind=Y` finishes a `NEW_FILE`
load (`Req_Stat` reaches `RULES_PASSED`/`EXTRACTED`), the same pipeline
step that writes that CRC row also inserts an Override row for it,
`Apprvl_Stat=PENDING_REVIEW`. This happens **every time**, whether it's
the very first file this source has ever sent, or its fifth — the
pipeline has no way to know in advance whether a new file should replace
the current anchor, so every candidate gets logged and queued for a human
to decide.

**Nothing happens to an Override row on days it isn't directly involved
in.** A `CARRIED_FORWARD` day doesn't touch Override at all — no new row,
no update to the existing one. The row only changes on an explicit event:
a human approving or revoking it, or the automatic supersession described
next.

**Governance — only one `APPROVED` row per (project, table, source, run
type, reporting period), enforced at write time, not just by convention.**
The grouping key is `(Project_Cd, Table_Nm, Src_Cd, Run_Ty,
Rpt_Start_Dt_Key, Rpt_End_Dt_Key)` — which is exactly why those six columns
are denormalized onto the Override row itself (see the DDL) rather than
requiring a join back to CRC. `ux_override_one_approved_per_group` is a
partial unique index over that key, filtered to `Apprvl_Stat='APPROVED'`
rows only — the database itself rejects a second `APPROVED` row in the
same grouping. In practice this means the approval action for a new
anchor is a **single transaction**: flip the currently-approved row (if
any) to `SUPERSEDED`, then insert/update the new row to `APPROVED`. If
that ordering were skipped, the index would simply reject the write.

**Subsequent runs follow whichever anchor is currently `APPROVED` —
automatically, every day, by re-checking the grouping.** No CRC row is
ever edited retroactively when the anchor changes; each day's own run
looks up the grouping's current `APPROVED` row at the moment it runs and
sets `Used_Btch_ID` accordingly. That's the entire mechanism behind Day 6
in Example A picking up Anchor #2 without anyone touching Days 1–5.

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
    UNIQUE (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Req_Dt_Key)
);

CREATE TABLE ComplianceBatchOverride (
    Ovrd_ID              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    Req_ID                BIGINT       NOT NULL REFERENCES ComplianceRequestControl(Req_ID),
    -- denormalized from the originating CRC row at insert time, so the
    -- "one active anchor per grouping" rule can be enforced on this table
    -- directly, without a join back to CRC on every check
    Project_Cd             VARCHAR(50)  NOT NULL,
    Table_Nm               VARCHAR(50)  NOT NULL,
    Src_Cd                 VARCHAR(50)  NOT NULL,
    Run_Ty                 VARCHAR(20)  NOT NULL,
    Rpt_Start_Dt_Key        DATE         NOT NULL,
    Rpt_End_Dt_Key          DATE         NOT NULL,
    Btch_ID               VARCHAR(120) NOT NULL,
    Used_Btch_ID           VARCHAR(120),
    Override_Ty            VARCHAR(30)  NOT NULL,  -- CARRY_FORWARD_REUSE / LATE_ARRIVAL_REOPEN / CORRECTION_REOPEN
    Prior_Btch_ID           VARCHAR(120),           -- snapshot of CRC's Btch_ID before this override, if any
    Prior_Resolution_Ty      VARCHAR(20),           -- snapshot of CRC's Resolution_Ty before this override
    Apprvl_Stat            VARCHAR(20)  NOT NULL,  -- PENDING_REVIEW / APPROVED / REVOKED / SUPERSEDED
    Apprvd_By               VARCHAR(100),
    Apprvd_Dtts             TIMESTAMP,
    Revoked_By              VARCHAR(100),
    Revoked_Dtts             TIMESTAMP,
    Revocation_Rsn           TEXT,
    History                VARCHAR(2000) NOT NULL,
    UNIQUE (Req_ID)
);

-- Enforces "at most one APPROVED anchor at a time" per (project, table,
-- source, run type, reporting period) — see §5. Approving a new anchor
-- must flip the old APPROVED row to SUPERSEDED in the SAME transaction,
-- or this index rejects the insert/update.
CREATE UNIQUE INDEX ux_override_one_approved_per_group
    ON ComplianceBatchOverride (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)
    WHERE Apprvl_Stat = 'APPROVED';

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

## Worked example A — carry-forward table (PROGAUDIT / ODAG1, FDR 2001, `Run_Ty=CMS`, reporting period Jan 1–Jan 31, daily runs Jan 1–6)

`Run_Ty=CMS` here is a fixed multi-week program-audit reporting period
(`Rpt_Start_Dt_Key=2026-01-01`, `Rpt_End_Dt_Key=2026-01-31`) — every row
below carries those same two values; only `Req_Dt_Key` (the daily run
date) moves. That fixed pair is exactly the grouping key that governs
which anchor is "active" — see §5.

| Day | CRC row (`Req_Dt_Key`) | Override row action | Why |
|---|---|---|---|
| 1 | `NEW_FILE`, `Used_Btch_ID=self`. Closes end of day. | **Auto-created (Anchor #1)**: `Override_Ty=CARRY_FORWARD_REUSE`, `PENDING_REVIEW` | New complete file load, source is carry-forward eligible |
| 2 | `MISSING`, `Used_Btch_ID=NULL`. Closes end of day. | Analyst approves later that day: Anchor #1 → `APPROVED`, `Apprvd_By=jsmith` | Day-2's run checked for a currently-`APPROVED` anchor in this grouping — found none (Anchor #1 still `PENDING_REVIEW`) — so reuse was **blocked** and the day is marked `MISSING` even though no file was ever expected that day |
| 3 | `CARRIED_FORWARD`, `Used_Btch_ID=Day1`. Closes end of day. | No change | Day-3's run does the *same* check Day-2's did — this time it finds Anchor #1 `APPROVED` (the approval landed sometime after Day-2's run, before Day-3's), so it carries forward automatically |
| 4 | `NEW_FILE`, `Used_Btch_ID=self`. Closes end of day. | **Auto-created (Anchor #2)**: `Override_Ty=CARRY_FORWARD_REUSE`, `PENDING_REVIEW` | A fresh file lands — every complete load for this source gets its own Override row, regardless of whether an approved anchor already exists |
| 5 | `CARRIED_FORWARD`, `Used_Btch_ID=Day1` (Anchor #1 is still the only `APPROVED` one). Closes end of day. | Analyst approves Anchor #2: `APPROVED`. **In the same transaction, Anchor #1 is auto-flipped to `SUPERSEDED`** | Only one `APPROVED` row is allowed per grouping — approving #2 forces #1 out, it isn't a manual second step |
| 6 | `CARRIED_FORWARD`, **`Used_Btch_ID=Day4`** (Anchor #2 is now the `APPROVED` one). Closes end of day. | No change | Day-6's run re-checks the grouping and now finds Anchor #2 — the switch is picked up automatically, no CRC backfill needed |

**Result: 6 CRC rows (one per day, never versioned), 2 Override rows**
(Anchor #1 ends `SUPERSEDED`, Anchor #2 ends `APPROVED`). Anchor #1's
`History`: *"Day 1: row created, PENDING_REVIEW | Day 2: UPDATE → APPROVED
by jsmith | Day 5: UPDATE → SUPERSEDED (Anchor from Day 4 batch approved
for the same reporting period)."*

## Worked example B — non-carry-forward table (PARTCODR / ROPENS, `Run_Ty=DAILY`, 3 FDRs, Jan 5)

Single business date, `Rpt_Start_Dt_Key=Rpt_End_Dt_Key=2026-01-05` on
every row (this run type reports one day at a time, unlike Example A's
fixed month-long window), `Carry_Fwd_Elig_Ind=N`, so Override is only ever
used for reopening — never for reuse. Using `DAILY` here alongside
Example A's `CMS` is deliberate: the framework treats `Run_Ty` as just
another grouping dimension, not something tied to a single cadence
pattern.

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
