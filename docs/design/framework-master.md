# CMS Compliance Framework — Master Design

**This is the current, authoritative reference.** It consolidates every
requirement and design decision made across the framework discussion into
one place, gives the final DDLs, walks the full pipeline end to end, and
closes out three gaps found while reviewing every scenario together (§6).

`schema-design.md` and `reuse-and-late-arrival.md` are earlier drafts,
kept for history — both are superseded by this document.

## 1. Requirements, consolidated

- **CRC (`ComplianceRequestControl`) is current-state only.** One row per
  `(Project_Cd, Table_Nm, Src_Cd, Run_Ty, Req_Dt_Key)`, forever. Corrections
  update that row in place — never versioned, never duplicated.
- **CRC carries its reporting period** (`Rpt_Start_Dt_Key`/`Rpt_End_Dt_Key`)
  alongside `Req_Dt_Key` — the two are independent. A `Run_Ty` can report a
  single day at a time (`Rpt_Start=Rpt_End=Req_Dt_Key`) or a fixed
  multi-week window shared by every daily row within it.
- **Every day gets a row, on schedule, regardless of outcome.** Missing a
  file never blocks the next date's row from being created and processed.
- **Batch close is schedule-driven, not completion-driven.** At a date's
  SLA cutoff, its CRC row(s) and the date's extract close — `PARTIAL`
  closes exactly like `COMPLETE`. A closed row accepts no further writes
  except through an approved Override.
- **`ComplianceExtractControl` is a separate, date-level, current-state
  table** — one row per `(Project_Cd, Table_Nm, Req_Dt_Key)`, no source
  column, tracking whether that date's CMS submission is `PARTIAL` or
  `COMPLETE` and whether it's closed.
- **Resolution priority**, evaluated fresh every run:
  1. A currently-`APPROVED`, still-valid anchor exists for the grouping →
     use it (`CARRIED_FORWARD`), **even if a file also arrived today**. The
     arriving file is received and logged, not promoted.
  2. No active/valid anchor, but a file arrived today → use it (`NEW_FILE`).
  3. Neither → `MISSING`.
- **Override (`ComplianceBatchOverride`) creation and update rules:**
  - No active candidate for the grouping + file arrives → **insert** new
    row, `PENDING_REVIEW`.
  - Active candidate is `PENDING_REVIEW` + another file arrives → **update
    the same row in place** to the newer candidate. Never a second row.
  - Active candidate is `APPROVED` (carry-forward reuse) + file arrives →
    row untouched; file logged and set aside.
  - A new anchor can only start once the current one leaves the active
    set — `REVOKED` or `EXPIRED` (never simply by a new file showing up).
  - Approval **requires** `Reuse_Valid_Thru_Dt_Key` — how long the anchor
    may govern before it must be re-approved, even if never revoked.
- **Late arrival / correction reopen** — a file landing after its CRC row
  is closed. `Override_Ty` distinguishes `LATE_ARRIVAL_REOPEN` (prior state
  was `MISSING`) from `CORRECTION_REOPEN` (prior state was `NEW_FILE`, data
  was wrong). Fully automatic detection (file naming pattern resolves the
  reporting date) — no `ComplianceRequestInTake` row for this path; that
  table stays reserved for genuinely business-initiated asks.
- **Files that were never expected are rejected before they reach CRC or
  Override at all** — unrecognized source, unscheduled date, or an exact
  duplicate of what's on file. Quarantined, audit-logged only.
- **Governance grouping key**: `(Project_Cd, Table_Nm, Src_Cd, Run_Ty,
  Rpt_Start_Dt_Key, Rpt_End_Dt_Key)`. At most one *active* Override row
  (`PENDING_REVIEW` or `APPROVED`) per grouping, enforced by a partial
  unique index — not convention.

## 2. Tables & final DDLs

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
    Rpt_Start_Dt_Key      DATE         NOT NULL,
    Rpt_End_Dt_Key        DATE         NOT NULL,
    Btch_ID              VARCHAR(120) NOT NULL,
    Cmplnc_Vrsn           VARCHAR(10),
    Req_Stat             VARCHAR(30)  NOT NULL,
    File_Received_Ind     SMALLINT     NOT NULL DEFAULT 0,
    Received_File_Ref      VARCHAR(500),           -- S3 path of whatever arrived, even if NOT promoted (see G1, §6)
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
    -- PENDING_REVIEW — NOT a fixed reference to whichever row first
    -- created this override.
    Project_Cd             VARCHAR(50)  NOT NULL,  -- denormalized from the
    Table_Nm               VARCHAR(50)  NOT NULL,  -- originating CRC row at
    Src_Cd                 VARCHAR(50)  NOT NULL,  -- insert/update time, so
    Run_Ty                 VARCHAR(20)  NOT NULL,  -- the grouping rule below
    Rpt_Start_Dt_Key        DATE         NOT NULL,  -- can be enforced here
    Rpt_End_Dt_Key          DATE         NOT NULL,  -- directly, no CRC join
    Btch_ID               VARCHAR(120) NOT NULL,
    Used_Btch_ID           VARCHAR(120),
    Override_Ty            VARCHAR(30)  NOT NULL,  -- CARRY_FORWARD_REUSE / LATE_ARRIVAL_REOPEN / CORRECTION_REOPEN
    Prior_Btch_ID           VARCHAR(120),           -- snapshot of CRC's Btch_ID before this override, if any
    Prior_Resolution_Ty      VARCHAR(20),           -- snapshot of CRC's Resolution_Ty before this override
    Apprvl_Stat            VARCHAR(20)  NOT NULL,  -- PENDING_REVIEW / APPROVED / REVOKED / SUPERSEDED / EXPIRED
    Apprvd_By               VARCHAR(100),
    Apprvd_Dtts             TIMESTAMP,
    Reuse_Valid_Thru_Dt_Key  DATE,                  -- required once APPROVED — see CHECK below
    Revoked_By              VARCHAR(100),
    Revoked_Dtts             TIMESTAMP,
    Revocation_Rsn           TEXT,
    History                VARCHAR(2000) NOT NULL,
    Updated_Dtts            TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT ck_override_valid_thru_required
        CHECK (Apprvl_Stat <> 'APPROVED' OR Reuse_Valid_Thru_Dt_Key IS NOT NULL)
);

-- At most one active candidate/anchor per grouping — the constraint that
-- makes "no new anchor until the current one is revoked or expired" a
-- hard rule rather than a process step someone could skip.
CREATE UNIQUE INDEX ux_override_one_active_per_group
    ON ComplianceBatchOverride (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key)
    WHERE Apprvl_Stat IN ('PENDING_REVIEW', 'APPROVED');

CREATE TABLE ComplianceExtractControl (
    Extract_ID            BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    Project_Cd             VARCHAR(50)  NOT NULL,
    Table_Nm               VARCHAR(50)  NOT NULL,
    Req_Dt_Key             DATE         NOT NULL,
    Extract_Stat           VARCHAR(20)  NOT NULL,  -- PARTIAL / COMPLETE
    Included_Src_Cds        VARCHAR(500),
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

## 3. End-to-end pipeline (every file, every day)

A file lands. This is the full decision sequence, in order — every earlier
discussion is a piece of this one flow:

1. **Parse the file name** against `File_Naming_Pattern` → resolve
   `(Src_Cd, Req_Dt_Key)`. Fails to parse → quarantine, audit
   `NAMING_VALIDATION_FAILED`. Stop.
2. **Check 1 — recognized source?** `(Project_Cd, Table_Nm, Src_Cd,
   Run_Ty)` must exist in the crosswalk. No → quarantine, audit
   `UNRECOGNIZED_SOURCE`. Stop.
3. **Check 2 — expected date?** A CRC row must already exist for this key
   (the scheduler creates rows ahead of time; a file never creates one).
   No → quarantine, audit `UNEXPECTED_DATE`. Stop.
4. **Check 3 — genuine duplicate?** File checksum matches
   `Received_File_Ref`'s content already on that CRC row → quarantine,
   audit `DUPLICATE_FILE_IGNORED`. Stop.
5. **Is the target CRC row closed (`Batch_Close_Ind=Y`)?**
   - **Yes** → this is a **reopen**, not ordinary processing. Look at the
     grouping's current Override row (if any):
     - No active row, or the file's own row's `Prior_Resolution_Ty` isn't
       relevant yet → insert new Override row, `Override_Ty` =
       `LATE_ARRIVAL_REOPEN` (row's `Resolution_Ty` was `MISSING`) or
       `CORRECTION_REOPEN` (`Resolution_Ty` was `NEW_FILE`),
       `PENDING_REVIEW`.
     - An `APPROVED` reopen row already exists for this exact date (a
       *second* correction is needed) → **update that same row in place**
       — new `Btch_ID`, `Prior_Btch_ID` snapshot refreshed,
       `Apprvl_Stat` reset to `PENDING_REVIEW`, `History` appended (§6,
       gap G2). Wait for approval; on approval, CRC row updates, extract
       reopens/regenerates/recloses (as in Worked Example B).
   - **No (open)** → run the resolution priority from §1:
     1. Active, valid `APPROVED` anchor in the grouping? → `CARRIED_FORWARD`
        from it; if a file also arrived, log it via `Received_File_Ref`
        and audit `FILE_RECEIVED_ANCHOR_ACTIVE`, do not promote.
     2. No active/valid anchor, file arrived → `NEW_FILE`, use it. If the
        source is `Carry_Fwd_Elig_Ind=Y`, this also drives the Override
        create/update-in-place rule from §1.
     3. Neither → `MISSING`.
6. **At the date's SLA cutoff** (independent of the above, runs on a
   schedule): every open CRC row for that date closes (`Batch_Close_Ind=Y`),
   `PARTIAL` or not. `ComplianceExtractControl` generates/regenerates from
   whatever's closed, then closes itself.
7. **Anchor expiry check** — runs at the start of each day's processing,
   before step 5, for every grouping with an `APPROVED` row: if
   `today > Reuse_Valid_Thru_Dt_Key`, flip that row to `EXPIRED` (audit
   `CARRY_FORWARD_ANCHOR_EXPIRED`) before any resolution logic runs, so no
   run can use a stale-but-still-`APPROVED`-looking anchor.

## 4. Worked examples

Unchanged from `reuse-and-late-arrival.md` §"Worked example A/B" — both
remain accurate under this design and aren't repeated here. The mock data
in `mock-data/cms_compliance_framework_tables.xlsx` implements them.

## 5. Naming & conventions (unchanged)

`PascalCase_With_Underscores`, `_Ind` booleans, `_Dtts`/`_Dt_Tm`
timestamps, `_Ty` types, `_Stat` status, `_Rsn` reasons. `Btch_ID`:
`{load-date}_{Project_Code}_{Table_Nm}_{Src_Cd}_{Run_Ty}_{Cmplnc_Vrsn}_{Seq}`
— the leading date is *when the row was last loaded*, which is why it
naturally changes on every in-place update without needing a version
suffix.

## 6. Failure points found reviewing every scenario together

Three real gaps surfaced only once every scenario was considered as one
system, not in isolation. All three are reflected in §1/§2/§3 above.

**G1 — an ignored file had nowhere to go.** Step 5's "file arrives while
an approved anchor is active → not promoted" was correct, but earlier
drafts never said where that file *went*. Left unaddressed, recovering it
after a revoke would mean asking the vendor to resend — needless, since
the file is already sitting in S3. **Fix:** `Received_File_Ref` on CRC
records the path of *whatever* arrived, promoted or not. On revoke, the
recovery step can re-run rules validation against that stored path
immediately rather than waiting on a fresh send — closing the gap between
"anchor revoked" and "new anchor available" instead of leaving the source
stuck in `MISSING` indefinitely.

**G2 — a second correction to an already-reopened date had no path.**
The original reopen design only covered one reopen per date. Since the
grouping's active-row uniqueness applies to reopens too (a date is its own
grouping under `Run_Ty=DAILY`), a *second* correction after the first was
already `APPROVED` would have hit the same unique index that blocks a
second carry-forward anchor — with no rule for what should happen instead.
**Fix:** distinguished by `Override_Ty` — `CARRY_FORWARD_REUSE` ignores a
new file while `APPROVED`; `LATE_ARRIVAL_REOPEN`/`CORRECTION_REOPEN`
instead **update the same row in place**, cycling back through
`PENDING_REVIEW`, since a reopen row was never meant to gate multiple
future days the way a carry-forward anchor does — it only ever represents
"the current best data for this one date."

**G3 — an anchor could be approved with no real expiry.** Making
`Reuse_Valid_Thru_Dt_Key` merely optional would have let an approver skip
it, silently recreating the exact Jan-vs-May staleness problem the column
exists to prevent. **Fix:** `ck_override_valid_thru_required` — a `CHECK`
constraint, not a UI reminder — makes it impossible for a row to reach
`APPROVED` without one.

**Considered and intentionally left as-is:**
- *Retroactive correction of already-closed, already-extracted dates
  that carried an anchor now revoked* — deliberately **not** automatic.
  Revocation only affects *future* resolution (§1); a past date that
  already submitted under that anchor stays as submitted unless someone
  explicitly reopens *that specific date* through the same
  `LATE_ARRIVAL_REOPEN`/`CORRECTION_REOPEN` path. Auto-correcting history
  the moment an anchor is revoked would silently rewrite what was already
  told to CMS — exactly what this framework is built to avoid.
- *Concurrent file arrivals for the same grouping* — the partial unique
  index (`ux_override_one_active_per_group`) plus each write being a
  single transaction means a second concurrent insert is rejected outright
  rather than racing; the losing writer retries as an update against
  whatever the winner left in place.
