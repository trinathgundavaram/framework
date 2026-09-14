# Reuse, late arrival, correction, and batch close — concept guide

> **Note:** superseded by [`framework-master.md`](framework-master.md), which is now the authoritative reference (final DDLs, the full pipeline, and a failure-points review). The two worked examples below are still accurate and are referenced from there directly.


This is the plain-language + DDL reference for how `ComplianceRequestControl`,
`ComplianceBatchOverride`, and `ComplianceExtractControl` work together. Two
compact worked examples cover every scenario discussed: a carry-forward
table under `Run_Ty=CMS` (6 days, 1 FDR, covering in-place candidate
replacement, approval with an expiry, an ignored file while an anchor is
active, and revoke-gated anchor replacement) and a
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
  closed date can be touched again**. Auto-created only when no candidate
  is currently active for that grouping; updated in place while a
  candidate is still under review; approval carries a **validity cutoff**
  (`Reuse_Valid_Thru_Dt_Key`) beyond which it can no longer be reused even
  though its status stays `APPROVED`. Automatically kept to **at most one
  active (`PENDING_REVIEW` or `APPROVED`) row per (project, table, source,
  run type, reporting period)**.
- **`ComplianceExtractControl`** — current-state, **one row per
  (table, date)**, no source column. Tracks whether that date's CMS
  submission is complete and whether it's locked.

## The four concepts

### 1. Carry-forward reuse
Some sources are allowed to let one approved file stand in for several
days' worth of data (`Carry_Fwd_Elig_Ind = Y` on the crosswalk table).
Every daily run resolves a source's batch in this priority order:

1. **Is there a currently-`APPROVED`, still-valid anchor for this
   grouping** — `Apprvl_Stat='APPROVED'` and `Req_Dt_Key <=
   Reuse_Valid_Thru_Dt_Key`? → **use it**, regardless of whether a file
   also physically arrived today. `Resolution_Ty=CARRIED_FORWARD`,
   `Used_Btch_ID`=the anchor's batch. If a file *did* arrive today, it is
   received (`File_Received_Ind=Y`) but **not promoted** — an audit event
   is logged (`FILE_RECEIVED_ANCHOR_ACTIVE`) so it's visible that a file
   showed up but was intentionally set aside, and it is *not* lost —
   whoever wants it considered has to revoke the current anchor first.
2. **No active/valid anchor, but a file arrived today** → use it.
   `Resolution_Ty=NEW_FILE`, `Used_Btch_ID=self`.
3. **Neither** → `MISSING`.

This is a deliberate reversal from "today's file always wins": once an
anchor is approved, it's what CMS is being told to treat as authoritative
for every day in its window, and swapping it out for whatever happens to
land next is exactly the kind of silent change Override exists to
prevent. Only a human revoking the anchor reopens the door to a new one.

**How the Override row itself gets written, step by step:**

- **No active row exists for the grouping** (none at all yet, or the most
  recent one is `REVOKED`/`SUPERSEDED`/`EXPIRED`) **and a file arrives**
  → **insert** a new row, `PENDING_REVIEW`, `Req_ID`/`Btch_ID` = today's
  CRC row/batch. This is the *only* condition under which a new row gets
  created — concretely, if Anchor #1 is still `PENDING_REVIEW` or
  `APPROVED`, a file arriving on any later day (Day 4, Day 5, ...) does
  **not** spawn Anchor #2. A new anchor can only start once Anchor #1
  has reached `REVOKED` (or expired/superseded).
- **An active row exists and is `PENDING_REVIEW`, and a new file arrives**
  → **update that same row in place** — its `Req_ID`, `Btch_ID`, and
  `Used_Btch_ID` move to today's file, and `History` gets a line noting
  the candidate was replaced while still under review. No new row. Nothing
  has been decided yet, so there's only ever one thing to decide on.
- **An active row exists and is `APPROVED`** → the row is untouched.
  Today's file (if any) is logged and set aside per step 1 above, not fed
  into Override at all.
- **Approval action** (a human decision, always) → the row updates to
  `Apprvl_Stat=APPROVED`, `Apprvd_By`/`Apprvd_Dtts` set, and critically
  **`Reuse_Valid_Thru_Dt_Key` is set at the same time** — see below.
- **Revocation** → `Apprvl_Stat=REVOKED`. This is what frees the grouping
  up for a brand-new anchor on a subsequent file arrival.

**`Reuse_Valid_Thru_Dt_Key` — why an `APPROVED` status alone isn't enough.**
Grouping is keyed on `(Project_Cd, Table_Nm, Src_Cd, Run_Ty,
Rpt_Start_Dt_Key, Rpt_End_Dt_Key)` — reporting *dates*, not a specific
execution cycle. If a request for Jan 1–Jan 31 is run in January, and a
*separate* request naming the exact same Jan 1–Jan 31 window gets kicked
off in May, both fall into the same grouping — without an expiry, the May
run would silently reuse January's five-month-old approved file. This
column is set by the approver at approval time (commonly defaulted to the
grouping's own `Rpt_End_Dt_Key`, but it's an explicit, overridable field —
an approver can shorten it to force re-review sooner). Once
`Req_Dt_Key > Reuse_Valid_Thru_Dt_Key`, the anchor stops qualifying at
step 1 above; the run that first notices this auto-flips the row's
`Apprvl_Stat` to `EXPIRED` (logged, not silent) and falls through to
step 2/3 like any other non-active anchor.

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

### 5. How this is enforced, not just followed by convention

**The grouping key** is `(Project_Cd, Table_Nm, Src_Cd, Run_Ty,
Rpt_Start_Dt_Key, Rpt_End_Dt_Key)` — which is why those six columns are
denormalized onto the Override row itself (see the DDL) rather than
requiring a join back to CRC on every check.

**`ux_override_one_active_per_group`** is a partial unique index over that
key, filtered to rows where `Apprvl_Stat IN ('PENDING_REVIEW','APPROVED')`
— the database itself rejects a second active row in the same grouping.
This is what makes "no new anchor until the current one is revoked" a
hard constraint rather than a process someone could accidentally skip:
there is nowhere to insert a competing row while one is still
`PENDING_REVIEW` or `APPROVED`. The only way to create a new row is for
the existing one to first move out of the index's filter — `REVOKED` or
`EXPIRED`.

**Nothing happens to the row on a day it isn't directly involved in.** A
`CARRIED_FORWARD` day — whether or not a file happened to arrive and get
set aside — never touches Override. The row only changes on an explicit
event: a candidate being replaced while still pending, an approval (with
its `Reuse_Valid_Thru_Dt_Key`), a revocation, or an automatic expiry.

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
    -- Req_ID/Btch_ID move to the latest candidate's CRC row while
    -- PENDING_REVIEW (see the "update in place" rule) — they are NOT a
    -- fixed reference to whichever row first created this override.
    -- denormalized from the originating CRC row at insert/update time, so
    -- the "one active candidate per grouping" rule can be enforced on this
    -- table directly, without a join back to CRC
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
    Apprvl_Stat            VARCHAR(20)  NOT NULL,  -- PENDING_REVIEW / APPROVED / REVOKED / SUPERSEDED / EXPIRED
    Apprvd_By               VARCHAR(100),
    Apprvd_Dtts             TIMESTAMP,
    Reuse_Valid_Thru_Dt_Key  DATE,                  -- set when Apprvl_Stat -> APPROVED; last Req_Dt_Key this anchor may serve
    Revoked_By              VARCHAR(100),
    Revoked_Dtts             TIMESTAMP,
    Revocation_Rsn           TEXT,
    History                VARCHAR(2000) NOT NULL,
    Updated_Dtts            TIMESTAMP    NOT NULL DEFAULT now()
);

-- Enforces "at most one active candidate/anchor at a time" per (project,
-- table, source, run type, reporting period) — see §5. This is what makes
-- "no new anchor until the current one is revoked or expired" a hard
-- constraint: there's nowhere to insert a second row while one is still
-- PENDING_REVIEW or APPROVED in the same grouping.
CREATE UNIQUE INDEX ux_override_one_active_per_group
    ON ComplianceBatchOverride (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)
    WHERE Apprvl_Stat IN ('PENDING_REVIEW', 'APPROVED');

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
date) moves.

| Day | CRC row (`Req_Dt_Key`) | Override row action | Why |
|---|---|---|---|
| 1 | `NEW_FILE`, `Used_Btch_ID=self`. Closes end of day. | **No active row exists → insert**: `Override_Ty=CARRY_FORWARD_REUSE`, `PENDING_REVIEW`, `Btch_ID=Day1` | First file for this grouping |
| 2 | `NEW_FILE`, `Used_Btch_ID=self` (a second file arrives). Closes end of day. | **Active row is `PENDING_REVIEW` → update the same row in place**: `Btch_ID`/`Used_Btch_ID`/`Req_ID` move to Day 2's batch. `History` appended. **No new row.** | Nothing's been decided yet — a newer candidate simply replaces the pending one |
| 3 | `MISSING`, `Used_Btch_ID=NULL`. Closes end of day. | Analyst approves later that day: `APPROVED`, `Apprvd_By=jsmith`, **`Reuse_Valid_Thru_Dt_Key=2026-01-31`** set | Day-3's run found the anchor still `PENDING_REVIEW` at run time — blocked, `MISSING` |
| 4 | A file **arrives**, but is **not used**: `File_Received_Ind=Y`, `Resolution_Ty=CARRIED_FORWARD`, `Used_Btch_ID=Day2` (the approved anchor). Closes end of day. | No change — the approved row is left untouched | Approved anchor is active and still within its validity window — the new file is logged (`FILE_RECEIVED_ANCHOR_ACTIVE`) and set aside, not promoted |
| 5 | `MISSING`, `Used_Btch_ID=NULL`. Closes end of day. | Analyst revokes: `REVOKED`, `Revoked_By=mgarcia`, `Revocation_Rsn` set | A real issue was found — revocation is the only thing that reopens the grouping to a new anchor |
| 6 | `NEW_FILE`, `Used_Btch_ID=self`. Closes end of day. | **Anchor #1 row is `REVOKED` → grouping is free → insert Anchor #2**: `PENDING_REVIEW`, `Btch_ID=Day6` | This is the first file since revocation — only now is a new anchor allowed to start |

**Result: 6 CRC rows (one per day, never versioned), 2 Override rows**
(Anchor #1's single row lived through `PENDING_REVIEW`→updated→`APPROVED`→
`REVOKED`, never duplicated; Anchor #2 starts fresh on Day 6, still
`PENDING_REVIEW`). Anchor #1's `History`: *"Day 1: row created,
PENDING_REVIEW (candidate: Day 1 batch) | Day 2: candidate replaced with
Day 2's batch, still PENDING_REVIEW | Day 3: UPDATE → APPROVED by jsmith,
valid through 2026-01-31 | Day 5: UPDATE → REVOKED by mgarcia — data
quality issue found."*

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
