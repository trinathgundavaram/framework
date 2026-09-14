# CMS Compliance Framework — Orchestration & Flow

Companion to [`framework-master.md`](framework-master.md) (the design
reference — requirements, DDLs, failure points). This document is the
**process view**: what actually runs, in what order, on what table and
what AWS service, for every file and every day — daily and manual-flag
sequencing (§1, §1a), the per-file decision logic both as a Step
Functions flow (§2) and as a table-level ERD (§2a), that same logic
redrawn around where a file's data actually ends up (§3), a fully
worked reopen example with concrete before/after row values (§4), the
Override state machine (§5), and the AWS service mapping (§6).

## 1. Daily orchestration — who runs what, when

```mermaid
sequenceDiagram
    participant EB as EventBridge (schedule)
    participant SCH as Scheduler (Lambda)
    participant S3 as S3 (inbound)
    participant INT as Intake (Lambda)
    participant SF as Step Functions (per-file workflow)
    participant CRC as ComplianceRequestControl
    participant FD as ComplianceRequestFileDetail
    participant OVR as ComplianceBatchOverride
    participant EXT as ComplianceExtractControl
    participant AUD as CMS_ComplianceExceptionsAudit

    EB->>SCH: Daily trigger (per Run_Ty cadence)
    SCH->>CRC: Create today's row(s) for every active (Project,Table,Src,Run_Ty)
    Note over SCH,OVR: Expiry sweep — any APPROVED row past Reuse_Valid_Thru_Dt_Key<br/>flips to EXPIRED before anything else runs (§7 of framework-master.md)

    S3-->>INT: File lands (event notification)
    INT->>SF: Start per-file workflow
    SF->>SF: Parse filename -> (Src_Cd, Req_Dt_Key)
    SF->>CRC: Checks 1-3 (recognized source, expected date, not a duplicate -<br/>duplicate check reads FD's latest Received_File_Ref for this Req_ID)
    alt any check fails
        SF->>AUD: Log rejection event, quarantine file
    else target CRC row is closed
        SF->>OVR: Insert/update reopen row (LATE_ARRIVAL_REOPEN / CORRECTION_REOPEN)
        Note over OVR: Waits for human approval
    else target CRC row is open
        SF->>OVR: Read grouping's active row (if any)
        SF->>CRC: Apply resolution priority, update CRC row in place
        SF->>FD: Log the event (FILE_RECEIVED / FILE_LOADED_NOT_PROMOTED)
        opt source is carry-forward eligible
            SF->>OVR: Insert new / update-in-place / leave-untouched per §1 rule
        end
    end

    EB->>SF: SLA-cutoff trigger (per date)
    SF->>CRC: Close every open row for that date (Batch_Close_Ind=Y)
    SF->>FD: Log BATCH_CLOSED (Batch_Closed_By)
    SF->>EXT: Generate/regenerate extract from currently-closed CRC rows
    SF->>EXT: Close extract (Extract_Close_Ind=Y)
```

## 1a. The manual flag path (independent of the sequence above)

```mermaid
sequenceDiagram
    participant BIZ as Business user
    participant IT as ComplianceRequestInTake
    participant SYNC as Sync (Lambda, event-driven off IT inserts)
    participant FD as ComplianceRequestFileDetail
    participant AUD as CMS_ComplianceExceptionsAudit
    participant VEND as Vendor

    Note over BIZ: Reviews an extract (or a CMS rejection),<br/>decides a submitted batch's DATA is wrong -<br/>the pipeline validated it as structurally fine at load time<br/>and has no way to know otherwise (G4)
    BIZ->>IT: Insert row, Req_Ty=CORRECTION_REQUEST
    IT->>SYNC: New row event
    SYNC->>FD: Log CORRECTION_FLAGGED (Flagged_By, Flag_Rsn) - CRC itself untouched
    SYNC->>AUD: Log DATA_QUALITY_ISSUE_FLAGGED
    SYNC->>IT: Processed_Ind=Y
    BIZ->>VEND: Chase corrected file (outside the system)
    VEND-->>FD: Corrected file eventually lands -> normal §2 flow (F=Yes branch)
    Note over FD: A matching CORRECTION_FLAG_CLEARED row is logged only<br/>when the REAL reopen (CORRECTION_REOPEN) actually fires<br/>and is approved - the flag alone never changes CRC, Override, or the extract
```

## 2. Per-file decision flow (the logic inside the Step Functions workflow)

```mermaid
flowchart TD
    A[File lands in S3] --> B{Filename parses?}
    B -- No --> R1[Quarantine<br/>Audit: NAMING_VALIDATION_FAILED]
    B -- Yes --> C{Source recognized<br/>in crosswalk?}
    C -- No --> R2[Quarantine<br/>Audit: UNRECOGNIZED_SOURCE]
    C -- Yes --> D{CRC row exists for<br/>this Src/Req_Dt_Key?}
    D -- No --> R3[Quarantine<br/>Audit: UNEXPECTED_DATE]
    D -- Yes --> E{Checksum matches latest<br/>FileDetail.Received_File_Ref<br/>for this Req_ID?}
    E -- Yes --> R4[Quarantine<br/>Audit: DUPLICATE_FILE_IGNORED]
    E -- No --> F{CRC row already<br/>Batch_Close_Ind=Y?}

    F -- Yes --> G{Grouping has an<br/>APPROVED reopen row<br/>for this exact date?}
    G -- No --> H[Insert Override row<br/>Override_Ty = LATE_ARRIVAL_REOPEN<br/>or CORRECTION_REOPEN<br/>PENDING_REVIEW]
    G -- Yes --> I[Update that SAME row in place<br/>new Btch_ID, back to PENDING_REVIEW<br/>G2 fix]
    H --> J[Wait for approval]
    I --> J
    J -- Approved --> K[CRC row updates in place<br/>Extract reopens, regenerates, recloses]

    F -- No --> L{Active grouping anchor<br/>APPROVED and<br/>Req_Dt_Key &lt;= Reuse_Valid_Thru_Dt_Key?}
    L -- Yes --> M[CRC: CARRIED_FORWARD from anchor<br/>File IS loaded/validated into staging<br/>FileDetail: FILE_LOADED_NOT_PROMOTED<br/>but Used_Btch_ID stays = anchor's batch<br/>Audit: FILE_RECEIVED_ANCHOR_ACTIVE<br/>G1 fix]
    L -- No --> N{Carry-forward<br/>eligible source?}
    N -- Yes --> O{Active Override row<br/>for this grouping?}
    O -- None --> P[CRC: NEW_FILE, self<br/>Override: INSERT new row, PENDING_REVIEW]
    O -- PENDING_REVIEW --> Q[CRC: NEW_FILE, self<br/>Override: UPDATE same row in place<br/>to this file's batch]
    N -- No --> S[CRC: NEW_FILE, self<br/>no Override row - not carry-forward eligible]
```

## 2a. Table-level design (how the tables connect)

```mermaid
erDiagram
  ComplianceDataSetSourceXwalk {
    string Project_Cd PK
    string Table_Nm PK
    string Src_Cd PK
    string Run_Ty PK
    smallint Carry_Fwd_Elig_Ind
  }
  ComplianceRequestControl {
    bigint Req_ID PK
    string Req_Dt_Key
    string Btch_ID
    string Req_Stat
    string Resolution_Ty
    string Used_Btch_ID
    smallint Batch_Close_Ind
  }
  ComplianceRequestFileDetail {
    bigint Detail_ID PK
    bigint Req_ID FK
    string Event_Ty
    string Entry_Ty
    string Received_File_Ref
    timestamp Event_Dtts
  }
  ComplianceBatchOverride {
    bigint Ovrd_ID PK
    bigint Req_ID FK
    string Override_Ty
    string Apprvl_Stat
    date Reuse_Valid_Thru_Dt_Key
  }
  ComplianceExtractControl {
    bigint Extract_ID PK
    string Req_Dt_Key
    string Extract_Stat
    smallint Extract_Close_Ind
  }
  ComplianceRequestInTake {
    string Intake_ID PK
    string Req_Ty
    string Requested_By
    smallint Processed_Ind
  }
  CMS_ComplianceExceptionsAudit {
    bigint Event_ID PK
    string Btch_ID
    string Event_Ty
    string Actor
  }

  ComplianceDataSetSourceXwalk ||--o{ ComplianceRequestControl : configures
  ComplianceRequestControl ||--o{ ComplianceRequestFileDetail : logs_events
  ComplianceRequestControl ||--o{ ComplianceBatchOverride : gated_by
  ComplianceRequestControl }o--|| ComplianceExtractControl : rolls_into
  ComplianceRequestInTake ||--o{ ComplianceRequestFileDetail : triggers_flag
  ComplianceRequestControl ||--o{ CMS_ComplianceExceptionsAudit : audited_in
```

Read this alongside §2: the flowchart shows *when* each table is touched, this
shows *how they're wired*. Three relationships worth calling out:

- **CRC ← FileDetail is 1-to-many** — this is the G5 split. CRC stays at
  exactly one row per `(Project_Cd, Table_Nm, Src_Cd, Run_Ty, Req_Dt_Key)`;
  every physical thing that happens to it (received, loaded-not-promoted,
  late, closed, flagged) is a separate append-only row here instead of a
  CRC column.
- **CRC ← Override is 1-to-many, but *at most one active row* at a time**
  — the fan-out looks unlimited in the ERD, but `ux_override_one_active_per_group`
  (a partial unique index, not visible in an ERD) caps it to one
  `PENDING_REVIEW`/`APPROVED` row per grouping. Older rows only exist once
  they've moved to `REVOKED`/`EXPIRED`.
- **CRC → ExtractControl is many-to-one** — every source's CRC row for a
  date feeds the *same* extract row for that date. This is the
  relationship that makes "3 FDRs required for one extract" possible at
  all.

## 3. Combined decision flow — where does the file's data actually end up?

This is §2's logic redrawn around a single question: after everything,
**does this file's data reach the extract or not?** Every path ends in
exactly one of four outcomes, colored by what happened to the data.

```mermaid
flowchart TD
    A[File lands in S3] --> B{Passes upstream<br/>checks? naming,<br/>source, date, duplicate}
    B -- No --> R[Reject and quarantine<br/>no CRC/Override write]

    B -- Yes --> C{Batch already<br/>closed?}
    C -- Yes --> D[Create or update Override<br/>PENDING_REVIEW]
    D --> E{Approved?}
    E -- Yes --> F[CRC + extract updated<br/>file data NOW used]
    E -- No --> D

    C -- No --> G{Approved, valid<br/>anchor active for<br/>this grouping?}
    G -- Yes --> H[File loaded to staging<br/>Received_File_Loaded_Ind=Y<br/>but NOT used for extract<br/>extract keeps using anchor]
    G -- No --> I[File becomes CRC's own data<br/>Used_Btch_ID = self<br/>used for extract]

    classDef reject fill:#F5C4B3,stroke:#993C1D,color:#4A1B0C
    classDef pending fill:#CECBF6,stroke:#534AB7,color:#26215C
    classDef used fill:#9FE1CB,stroke:#0F6E56,color:#04342C
    classDef notused fill:#FAC775,stroke:#854F0B,color:#412402
    class R reject
    class D pending
    class F,I used
    class H notused
```

The two `Yes` branches under "approved anchor active?" are the crux of
G1: a file can be perfectly valid, fully loaded, and still never feed the
extract — because *something else already governs that grouping's data*
until a human explicitly revokes it.

## 4. Worked example — reopen creation and approval, step by step

Concrete before/after values, using the actual mock-data case: FDR 1003,
`PARTCODR/ROPENS`, `Req_Dt_Key=2026-01-10`, `Run_Ty=DAILY`. The file
arrived on time and passed validation, but was later found to contain
incorrect data. Note `Btch_ID` throughout: it's built from `Req_Dt_Key`
(§5 of `framework-master.md`), so it stays **the same value across every
step** — identity, not a timestamp of when it was last touched.

**Step 0 — state before anything happens (post Jan-10 close)**

| Table | Row |
|---|---|
| CRC (`Req_ID=27`, say) | `Btch_ID=20260110_PARTCODR_ROPENS_1003_DAILY_2026_1`, `Resolution_Ty=NEW_FILE`, `Used_Btch_ID`=itself, `Batch_Close_Ind=Y` |
| Override | *(no row for this grouping — nothing to reopen yet)* |
| ExtractControl (`Req_Dt_Key=2026-01-10`) | `Extract_Stat=COMPLETE`, `Included_Src_Cds=[1001,1002,1003]`, `Extract_Close_Ind=Y` |

**Step 1 — Jan 12: business flags it (§1a, independent of the reopen itself)**

`ComplianceRequestInTake` gets a row (`Req_Ty=CORRECTION_REQUEST`,
`Requested_By=mgarcia`). Sync writes a `ComplianceRequestFileDetail` row
(`Event_Ty=CORRECTION_FLAGGED`) against `Req_ID=27`. **Nothing else
changes** — CRC, Override, and the extract are all still exactly as in
Step 0, including `Btch_ID`. This step exists purely so the gap between
"we noticed" and "it's fixed" is on record.

**Step 2 — Jan 14: corrected file arrives, gets intercepted at the "closed?" check**

Intake resolves the file to `Req_Dt_Key=2026-01-10`, looks up CRC
`Req_ID=27`, sees `Batch_Close_Ind=Y`. Per §2/§3, this routes to reopen,
not normal processing. Since `Resolution_Ty` was `NEW_FILE` (not
`MISSING`), `Override_Ty=CORRECTION_REOPEN` — a mechanical read, not a
decision.

| Table | Row written |
|---|---|
| Override (new row) | `Req_ID=27`, `Btch_ID=20260110_PARTCODR_ROPENS_1003_DAILY_2026_1` (same value as CRC's — it's the same batch identity, not a new one), `Override_Ty=CORRECTION_REOPEN`, `Prior_Btch_ID=20260110_..._1003..._1`, `Prior_Resolution_Ty=NEW_FILE`, `Apprvl_Stat=PENDING_REVIEW` |
| FileDetail | `Event_Ty=FILE_RECEIVED`, `Received_File_Ref=s3://.../1003/20260114-corrected.dat`, `Event_Dtts=2026-01-14 09:10:00` — this is where "when it actually arrived" now lives, since `Btch_ID` no longer carries it |

CRC row `Req_ID=27` is **not touched yet** — still shows the original
(incorrect) file as current.

**Step 3 — same day: `jsmith` reviews and approves**

This is the one step with no automatic path. Approval cascades three
writes in one transaction:

| Table | Before → After |
|---|---|
| Override | `Apprvl_Stat`: `PENDING_REVIEW` → `APPROVED`, `Apprvd_By=jsmith`, `Apprvd_Dtts=2026-01-14 10:15:00`, `History` appended |
| CRC (`Req_ID=27`) | `Btch_ID` **unchanged** — still `20260110_PARTCODR_ROPENS_1003_DAILY_2026_1`. What moves: `Used_Btch_ID` re-points at that same identity (now backed by the corrected file), `Batch_Close_Ind`: `Y` → `N`, then back to `Y` once the row closes again (still same `Req_ID` — never a new row) |
| FileDetail | new row, `Event_Ty=CORRECTION_FLAG_CLEARED` — closes the loop opened in Step 1 |
| ExtractControl (`Req_Dt_Key=2026-01-10`) | `Reopen_Ind`: `N`→`Y`, `Reopened_By=jsmith`. Regenerates: `Included_Src_Cds` still `[1001,1002,1003]`, now backed by the corrected 1003 data. `Extract_Close_Ind`: `Y`→`N`→`Y` again once regeneration completes |
| Audit | `CORRECTION_REOPEN_APPROVED` logged, referencing `Btch_ID=20260110_..._1` throughout — there's only ever one batch identity for this date, so the audit trail never has to reconcile two different IDs for the same thing |

**What this example demonstrates that the abstract rules don't**: the CRC
row's `Req_ID` *and* its `Btch_ID` never change across all three steps —
it's the same control-table row, identified the same way, from Jan 10
through Jan 14, just updated twice
(Step 0's initial write, Step 3's reopen). Everything that *did* need
more than one record — the flag, the file arrival, the approval, the
extract regeneration — lives in a different table each time, which is
exactly the layering §2a's ERD is describing.

## 5. Anchor lifecycle (the state machine behind `ComplianceBatchOverride`)

```mermaid
stateDiagram-v2
    [*] --> PENDING_REVIEW : file arrives, no active row in grouping
    PENDING_REVIEW --> PENDING_REVIEW : newer file arrives while still pending\n(same row updated, no new row)
    PENDING_REVIEW --> APPROVED : human approves\n(Reuse_Valid_Thru_Dt_Key required - hard CHECK constraint, §6 G3)
    APPROVED --> REVOKED : human revokes\n(only path back to an open grouping)
    APPROVED --> EXPIRED : today > Reuse_Valid_Thru_Dt_Key\n(auto, at start of daily run, before resolution)
    REVOKED --> [*] : grouping free - next file starts a brand-new PENDING_REVIEW row
    EXPIRED --> [*] : grouping free - same
    APPROVED --> APPROVED : file arrives while approved\n(loaded to staging, NOT promoted - row itself untouched, G1)
```

## 6. AWS service mapping

| Step | Service | Notes |
|---|---|---|
| Daily row creation, expiry sweep | Lambda, triggered by EventBridge | Runs once per `Run_Ty` cadence, before any file processing for the day |
| Manual data-quality flag sync (§1a) | Lambda, triggered by `ComplianceRequestInTake` insert event | The one part of the pipeline started by a human write, not a file or schedule |
| File-landed trigger | S3 event notification → Lambda | Starts the per-file Step Functions execution |
| Per-file decision flow (§2) | Step Functions | One execution per file; each decision node is a Lambda task or a Choice state reading `ComplianceDataSetSourceXwalk` / `ComplianceRequestControl` / `ComplianceBatchOverride` |
| Rules validation / staging load | Glue job, invoked from the Step Functions workflow | Runs regardless of whether the file will be promoted (G1) — a loaded-but-not-promoted file still passes through this |
| CRC / FileDetail / Override / ExtractControl writes | Lambda, using RDS for PostgreSQL | All tables' constraints (unique indexes, the `CHECK` on `Reuse_Valid_Thru_Dt_Key`) are enforced at the database layer, not just in application code |
| "Is this row currently late/flagged?" lookups | `vw_crc_current_flags` (a view, queried directly — no dedicated compute) | Answers what used to be plain CRC columns, computed from `ComplianceRequestFileDetail` on read instead of stored on write |
| SLA-cutoff close + extract generation | EventBridge scheduled trigger → Step Functions | Independent schedule from file arrival — fires whether or not every source reported in |
| Notifications (rejections, pending approvals, expiries) | SNS, fed by `CMS_ComplianceExceptionsAudit` inserts | Approval queue and rejection visibility both come from this one table |

## 7. What each diagram is answering

- **§1 (sequence)** — the daily rhythm: what's scheduled versus what's
  event-driven, and where the expiry sweep sits relative to file
  processing (before, always).
- **§1a (sequence)** — the one path in the whole system that starts with
  a human typing something in, not a file or a clock: flagging that
  already-submitted data is wrong, days or weeks before any fix exists
  (G4). Deliberately kept separate from Override — a flag alone changes
  nothing downstream.
- **§2 (flowchart)** — the exact branch every file takes, including the
  two fixes from `framework-master.md` §6 inline at the nodes where they
  apply (G1 at the "loaded but not promoted" node, G2 at the "update the
  same reopen row" node).
- **§2a (ERD)** — the schema-level companion to §2: not *when* each table
  is touched, but *how they're wired together*, including the two
  relationships an ERD alone can't fully capture (Override's one-active-
  row-per-grouping constraint, CRC's many-to-one into ExtractControl).
- **§3 (combined decision flow)** — §2 redrawn around one question only:
  does this file's data reach the extract or not. Every path terminates
  in one of four colored outcomes, so the answer is visible at a glance
  rather than requiring a trace through the full branch logic.
- **§4 (worked example)** — the abstract rules in §2/§3 made concrete:
  exact before/after row values across CRC, Override, FileDetail, and
  ExtractControl for one real reopen, end to end.
- **§5 (state machine)** — the Override row's lifecycle in isolation,
  since it's the one table whose *sequence* of states (not just current
  value) actually drives behavior elsewhere in the system.
- **§6 (service mapping)** — turns the above into an actual build list.
