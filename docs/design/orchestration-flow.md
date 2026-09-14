# CMS Compliance Framework — Orchestration & Flow

Companion to [`framework-master.md`](framework-master.md) (the design
reference — requirements, DDLs, failure points). This document is the
**process view**: what actually runs, in what order, on what AWS service,
for every file and every day.

## 1. Daily orchestration — who runs what, when

```mermaid
sequenceDiagram
    participant EB as EventBridge (schedule)
    participant SCH as Scheduler (Lambda)
    participant S3 as S3 (inbound)
    participant INT as Intake (Lambda)
    participant SF as Step Functions (per-file workflow)
    participant CRC as ComplianceRequestControl
    participant OVR as ComplianceBatchOverride
    participant EXT as ComplianceExtractControl
    participant AUD as CMS_ComplianceExceptionsAudit

    EB->>SCH: Daily trigger (per Run_Ty cadence)
    SCH->>CRC: Create today's row(s) for every active (Project,Table,Src,Run_Ty)
    Note over SCH,OVR: Expiry sweep — any APPROVED row past Reuse_Valid_Thru_Dt_Key<br/>flips to EXPIRED before anything else runs (§7 of framework-master.md)

    S3-->>INT: File lands (event notification)
    INT->>SF: Start per-file workflow
    SF->>SF: Parse filename -> (Src_Cd, Req_Dt_Key)
    SF->>CRC: Checks 1-3 (recognized source, expected date, not a duplicate)
    alt any check fails
        SF->>AUD: Log rejection event, quarantine file
    else target CRC row is closed
        SF->>OVR: Insert/update reopen row (LATE_ARRIVAL_REOPEN / CORRECTION_REOPEN)
        Note over OVR: Waits for human approval
    else target CRC row is open
        SF->>OVR: Read grouping's active row (if any)
        SF->>CRC: Apply resolution priority, update CRC row in place
        opt source is carry-forward eligible
            SF->>OVR: Insert new / update-in-place / leave-untouched per §1 rule
        end
    end

    EB->>SF: SLA-cutoff trigger (per date)
    SF->>CRC: Close every open row for that date (Batch_Close_Ind=Y)
    SF->>EXT: Generate/regenerate extract from currently-closed CRC rows
    SF->>EXT: Close extract (Extract_Close_Ind=Y)
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
    D -- Yes --> E{Checksum matches<br/>Received_File_Ref on file?}
    E -- Yes --> R4[Quarantine<br/>Audit: DUPLICATE_FILE_IGNORED]
    E -- No --> F{CRC row already<br/>Batch_Close_Ind=Y?}

    F -- Yes --> G{Grouping has an<br/>APPROVED reopen row<br/>for this exact date?}
    G -- No --> H[Insert Override row<br/>Override_Ty = LATE_ARRIVAL_REOPEN<br/>or CORRECTION_REOPEN<br/>PENDING_REVIEW]
    G -- Yes --> I[Update that SAME row in place<br/>new Btch_ID, back to PENDING_REVIEW<br/>G2 fix]
    H --> J[Wait for approval]
    I --> J
    J -- Approved --> K[CRC row updates in place<br/>Extract reopens, regenerates, recloses]

    F -- No --> L{Active grouping anchor<br/>APPROVED and<br/>Req_Dt_Key &lt;= Reuse_Valid_Thru_Dt_Key?}
    L -- Yes --> M[CRC: CARRIED_FORWARD from anchor<br/>File IS loaded/validated into staging<br/>Received_File_Loaded_Ind=Y<br/>but Used_Btch_ID stays = anchor's batch<br/>Audit: FILE_RECEIVED_ANCHOR_ACTIVE<br/>G1 fix]
    L -- No --> N{Carry-forward<br/>eligible source?}
    N -- Yes --> O{Active Override row<br/>for this grouping?}
    O -- None --> P[CRC: NEW_FILE, self<br/>Override: INSERT new row, PENDING_REVIEW]
    O -- PENDING_REVIEW --> Q[CRC: NEW_FILE, self<br/>Override: UPDATE same row in place<br/>to this file's batch]
    N -- No --> S[CRC: NEW_FILE, self<br/>no Override row - not carry-forward eligible]
```

## 3. Anchor lifecycle (the state machine behind `ComplianceBatchOverride`)

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

## 4. AWS service mapping

| Step | Service | Notes |
|---|---|---|
| Daily row creation, expiry sweep | Lambda, triggered by EventBridge | Runs once per `Run_Ty` cadence, before any file processing for the day |
| File-landed trigger | S3 event notification → Lambda | Starts the per-file Step Functions execution |
| Per-file decision flow (§2) | Step Functions | One execution per file; each decision node is a Lambda task or a Choice state reading `ComplianceDataSetSourceXwalk` / `ComplianceRequestControl` / `ComplianceBatchOverride` |
| Rules validation / staging load | Glue job, invoked from the Step Functions workflow | Runs regardless of whether the file will be promoted (G1) — a loaded-but-not-promoted file still passes through this |
| CRC / Override / ExtractControl writes | Lambda, using RDS for PostgreSQL | All three tables' constraints (unique indexes, the `CHECK` on `Reuse_Valid_Thru_Dt_Key`) are enforced at the database layer, not just in application code |
| SLA-cutoff close + extract generation | EventBridge scheduled trigger → Step Functions | Independent schedule from file arrival — fires whether or not every source reported in |
| Notifications (rejections, pending approvals, expiries) | SNS, fed by `CMS_ComplianceExceptionsAudit` inserts | Approval queue and rejection visibility both come from this one table |

## 5. What each diagram is answering

- **§1 (sequence)** — the daily rhythm: what's scheduled versus what's
  event-driven, and where the expiry sweep sits relative to file
  processing (before, always).
- **§2 (flowchart)** — the exact branch every file takes, including the
  two fixes from `framework-master.md` §6 inline at the nodes where they
  apply (G1 at the "loaded but not promoted" node, G2 at the "update the
  same reopen row" node).
- **§3 (state machine)** — the Override row's lifecycle in isolation,
  since it's the one table whose *sequence* of states (not just current
  value) actually drives behavior elsewhere in the system.
- **§4 (service mapping)** — turns the above into an actual build list.
