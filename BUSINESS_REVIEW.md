# Compliance batch framework: product and business review

Scope: the Airflow / Teradata copy at 0.3.41, read as a product that several projects (CMS Universes today, other
regulatory submissions later) run side by side, each with several tables, sources, run types and ad-hoc requests.
The question is not "does the code work" (the technical review in `REVIEW_RECOMMENDATIONS.md` covers that) but
"what will the business run into when more projects and use cases depend on it".

Each item gives what happens today, why it matters to the business, a recommendation and a rough effort
(S = under a day, M = a few days, L = a week or more, including testing). **Priority**: P1 = before the first
production submission, P2 = before a second project goes live, P3 = when convenient.

## Summary

| # | Area | Item | Priority | Effort |
|---|---|---|---|---|
| 1 | Readiness | Never run end to end on Teradata, the real NAS or Airflow 3 step DAGs | P1 | M |
| 2 | Readiness | Glue copy has fallen far behind the Airflow copy | P1 (decision) | S / L |
| 3 | Compliance | No snapshot of what was submitted; core and export rows are replaced in place | P1 | M |
| 4 | Compliance | Error logs on the NAS hold raw records (member data) | P1 | S |
| 5 | Compliance | Overrides, intake, reopen and releases are approved by convention, not enforced | P1 | M |
| 6 | Compliance | Who held, released or re-pulled a batch is not recorded (actor = SYSTEM) | P2 | S |
| 7 | Process | Rule failures are reported only; nothing stops bad data reaching the export | P1 (decision) | S |
| 8 | Process | No submission / sign-off step after the export | P2 | L |
| 9 | Process | No completeness gate across sources or tables of a submission | P2 | M |
| 10 | Process | Batches without data never close on their own and are not escalated (done in 0.3.42) | P1 | S |
| 11 | Process | Due dates and SLAs are calendar days; no business days or holidays | P2 | M |
| 12 | Process | One failing file or source delays every other one of the same run | P2 | S |
| 13 | Multi-project | Run types and source systems are global, not per project | P2 | M |
| 14 | Multi-project | One recipient list for every project's alerts (covered: DAGs cloned per project) | P2 | S |
| 15 | Multi-project | Projects share the step DAGs: one project's failure or long load affects all (covered: DAGs cloned per project) | P2 | S |
| 16 | Multi-project | Business time zone is fixed to Central | P3 | S |
| 17 | Config | Metadata changes apply at once to batches already in flight; no versioning | P2 | M |
| 18 | Config | Business controls are split between metadata tables and Airflow Variables | P2 | M |
| 19 | Config | Ad-hoc requests, overrides and calendars are entered with SQL | P2 | L |
| 20 | Data | No retention or purge of control, audit, staging, core, export or NAS archive | P2 | M |
| 21 | Data | Late or corrected data after export silently replaces what downstream may have taken | P2 | S |
| 22 | Data | Internal-source data pulled early is not refreshed if the source changes | P3 | S |
| 23 | Operations | No status view of the batches for business users | P2 | M |
| 24 | Operations | No source performance reporting (on time, late, missing, rejected) | P3 | S |
| 25 | Operations | Open technical items: retry limits, lock refresh, NAS clock (A3, A5, A9) | P2 | M |
| 26 | Operations | Large files: the faster load path is untested on Teradata | P2 | M |
| 27 | Product | Documentation is one long technical guide; no business user guide or runbook | P2 | M |
| 28 | Product | Many statuses, events and codes; no single glossary for business users | P3 | S |

## 1. Readiness and risk

**1. Not yet proven on the real platform (P1).** Everything has been tested against a PostgreSQL stand-in for
Teradata, a local folder or SMB test server for the NAS, and an Airflow 3.3.2 install for parsing the DAGs.
The step DAGs have never run end to end on Teradata, the NAS move / archive steps have never touched the real NAS,
and FASTLOAD, SQL Server sources, the SMTP connection and the GRE bridge have not run against the real systems.
*Business risk*: the first submission cycle is also the first real test. *Recommendation*: one full dress
rehearsal in DEV per project shape (file sources, internal sources, carry-forward, late arrival, reopen, export,
rules, close, notify) with a checklist signed off before production; keep the scenario list from the test suite as
the checklist.

**2. Two products, one maintained (P1, decision).** Since 0.3.34 every feature (internal sources, export,
release / hold, reopen, table-level batch ID, schedule codes, additive file checks, Airflow 3) went into the Airflow
copy only. The Glue / PostgreSQL copy is now a different product with different batch IDs and statuses.
*Business risk*: a team picking Glue gets an older, incompatible framework; fixes are made twice or not at all.
*Recommendation*: decide whether Glue is retired (mark it frozen in its README) or brought to parity (L).

## 2. Compliance and audit

**3. What was submitted cannot be reproduced (P1).** A reload, correction, late arrival or carry-forward deletes
and re-inserts the batch's core rows, and EXPORT_LOAD replaces the export rows of the period. The timeline records
*that* data changed, not *what* the data was. *Business risk*: an auditor or CMS asks "what exactly did you submit on
10/23" and the rows have since been replaced. *Recommendation*: freeze a copy at submission (an export history
table keyed by `Btch_ID`, source and a submission timestamp, or a versioned export with `Valid_From` / `Valid_To`),
plus the file name and checksum of what left the building.

**4. Member data in error logs (P1).** The error log written next to a rejected file has a `Record` column with the
whole failing line: member names, IDs, claim numbers. Airflow logs and emails are kept clean, but these files sit on
the NAS with the folder's permissions. *Recommendation*: a setting to mask or drop the record (keep file, line,
check, message), and an access review of the `ErrorLog` / `Error` / `Archive` folders.

**5. Approvals are not enforced (P1).** `ComplianceBatchOverride` rows (REUSE, LATE_ARRIVAL, CORRECTION, REOPEN),
intake requests and calendar rows are inserted with SQL; `Apprvl_Stat = 'APPROVED'` and `Reviewed_By` are typed by
whoever writes the row. Nothing stops the requester approving their own override, a backdated `Valid_Thru`, or a
REOPEN of a batch that was already submitted. *Recommendation*: a separate approval step (a second person, recorded
from the database user, not typed), a check that requester and approver differ, and an exceptions report of every
override applied per submission period.

**6. Actions without a name (P2).** `BATCH_RELEASED`, holds (an Airflow Variable), `batch=` re-pulls and manual
DAG triggers are recorded as `SYSTEM`. *Recommendation*: pass the Airflow user of a manual trigger into the
events (`Actor`), and record a hold being set or removed as a timeline event.

## 3. Business process coverage

**7. Rule failures do not stop anything (P1, decision).** FILE_RULES runs after the export and its failures are
"reported only" (as decided). The export, the close and anything downstream proceed with data that failed its
rules. *Recommendation*: decide per rule severity: blocking rules hold the batch's release / close until an
override, warnings stay report-only. The engine's ERROR vs WARN result already exists.

**8. The process stops at the export (P2).** There is no step for producing the submission file, sign-off,
transmission and acknowledgement. *Business risk*: the last and most visible part of the compliance process
happens outside the audit trail. *Recommendation*: a SUBMISSION step (build the file from the export batch, record
checksum, approver, sent time, acknowledgement) with its own statuses.

**9. No "submission complete" check (P2).** Each source batch closes on its own. Nothing says "ODAG1-5 for October
have all their sources delivered, carried forward or explicitly waived", which is what a submission needs.
*Recommendation*: a completeness view per table group / run type / period (sources expected from the crosswalk vs
delivered), used as the gate of item 8.

**10. Batches without data wait forever (P1; done in 0.3.42: BATCH_CLOSE closes them on the close date as
`DATA_NOT_PROVIDED` / `MISSING` with an emailed `SOURCE_MISSING_AT_CLOSE` alert).** BATCH_CLOSE only closes batches that have data (or a no-data file).
A source that sends nothing leaves its batch `REQUEST_CREATED` until someone runs `close-batch`; it shows in
`health` but nothing escalates. *Recommendation*: alert (NOTIFY) when a batch passes its due date without data, and
again after N days; optionally a run-type setting to close it as `DATA_NOT_PROVIDED` / `MISSING` automatically after
a grace period.

**11. Calendar days only (P2).** `SLA_Days`, due dates, the schedule codes and `Sched_Window_Days` count calendar
days. CMS and vendor commitments are usually business days; a holiday or weekend can make a due date fall on a day
nobody delivers. *Recommendation*: a business calendar table (holidays per project) and `SLA_Days` /
`Sched_Day_Cd` in business days (e.g. "3rd business day").

**12. One problem delays the rest (P2).** FILE_LOAD and SOURCE_LOAD process every file or source, then fail the
task if any one failed; the triggered steps (export, rules, notify) do not start for the good ones until the next
run (review item A7, still open). *Recommendation*: let the triggered steps start when at least one item succeeded
and report the failures separately, or make this a per-DAG choice.

## 4. Several projects on one framework

**13. Global run types and sources (P2).** `ComplianceRunType` (SLA, schedule, carry-forward, category) and
`ComplianceSourceSystem` have no project. Two projects that both want "MNT" but with different SLAs or schedules
must invent different run type codes, and the code appears in file names and batch IDs. A source ID means the same
source for every project. *Recommendation*: add `Project_Cd` (with `*` = every project, as the file check table
does) to run types, or a per-project override table for SLA / schedule / release.

**14. One alert list (P2; covered by cloning the DAGs per project: each project's NOTIFY Variable sets `project`
and its own `DEFAULT_NOTIFY_EMAILS`. Keep one NOTIFY without `project` for events that have no project, e.g. a file
that matches no file config).** Every audit event (rejected file, missing source, failed export, override applied) goes
to `DEFAULT_NOTIFY_EMAILS` of the NOTIFY run. Project B's team gets project A's alerts unless NOTIFY is split per
project with `runs` entries. Source owners (the FDR contacts who must resend a file) are never told.
*Recommendation*: recipients per project (and optionally per source / event type) in metadata.

**15. Shared step DAGs (P2; covered by cloning the DAGs per project, with `project` in every Variable and
`trigger_dags` naming the project's own clones).** One DAG per step serves every project (one task per `runs` entry, or one task for
all). A long FILE_LOAD for one project holds the DAG (`max_active_runs` 1) for all; a failure in one project's task
fails the run and stops the triggered DAGs for all (item 12). *Recommendation*: one task per project in every
step DAG (the `runs` mechanism already allows it) and triggers that do not depend on every task succeeding, or one
DAG set per project for projects with different calendars.

**16. Central time only (P3).** Business dates, due dates, `RELEASE_TIME` and timestamps are Central. Fine for the
current team; a project run from another region would need its own time zone.

## 5. Configuration management

**17. No configuration versions (P2).** Only the crosswalk is effective-dated. Changing a file config, target
columns, file checks, query or export config takes effect on the next run, also for batches already in flight
(e.g. a new header check rejects a resend for last month). *Recommendation*: effective dates on the configuration
rows that shape data (file / query config, target columns, file checks), and a change log.

**18. Business controls in two places (P2).** Schedules, `RELEASE_TIME`, `hold`, timed `runs` and recipients live in
Airflow Variables; run types, calendars, overrides and intake live in metadata tables. *Business risk*: a business
user cannot see in one place why a batch did not go out (a hold in a Variable is invisible in the tables).
*Recommendation*: move holds (and `RELEASE_TIME`) into a metadata table with dates and reasons; keep Variables for
technical settings.

**19. Operated through SQL (P2).** Ad-hoc requests, overrides, reopen, calendar rows and holds are SQL inserts or
JSON. *Business risk*: typos, missing approvals, wrong batch IDs (now table-level: every source of the batch).
*Recommendation*: a small request / approval front end (or at least stored procedures with validation) for the
business actions.

## 6. Data lifecycle

**20. Nothing is ever deleted (P2).** Control, timeline, audit, load and lock tables, staging, core, export and the
NAS `Archive` / `Error` / `ErrorLog` folders grow without limit. *Recommendation*: a retention policy per table and
folder (e.g. the record-retention period for submissions) and a purge step.

**21. Changes after export are silent downstream (P2).** A late arrival, correction or reopen re-exports the batch
and replaces its export rows. A consumer that already read the export is not told. *Recommendation*: a
re-export event / notification and an export version number per batch.

**22. Early pulls are not refreshed (P3).** Internal-source data pulled before the due date is not pulled again if
the source changes before the due date (a re-pull needs `batch=`). *Recommendation*: optionally re-pull on the due
date for sources flagged as "volatile".

## 7. Operations

**23. No status view (P2).** The batch statuses, holds, waiting pulls and exports are in tables and logs; business
users have no dashboard ("which sources are late for October MNT"). *Recommendation*: a reporting view per project /
run type / period (and an Airflow-independent page or BI report on it).

**24. No source performance reporting (P3).** The timeline has everything to measure on-time / late / missing /
rejected deliveries per source and period, but nothing reports it. Useful for vendor oversight of FDRs.

**25. Open technical items (P2).** From the technical review, still open: A3 (unlimited retries of technical
failures), A5 (locks are not refreshed during long loads), A9 (NAS file age compared with the worker's clock).

**26. Volumes (P2).** The default load sends rows in batches; the faster FASTLOAD path for 1.5 GB files is built but
untested on Teradata. *Recommendation*: measure with the largest real file in DEV before go-live.

## 8. Product

**27. Documentation (P2).** `AIRFLOW_DEPLOYMENT.md` (about 1,150 lines) is a complete technical reference; there is
no guide for business users (how to request an ad-hoc run, approve an override, release / hold, read the report) and
no operations runbook (what to do when a file is rejected, a source is missing, a lock is stuck).

**28. Vocabulary (P3).** Statuses (`REQUEST_CREATED` ... `DATA_NOT_PROVIDED`), resolutions, events (44),
override types and check codes are spread over the guide and the design notes. A one-page glossary for business
users would help.

## Suggested order

1. P1 items 1, 4, 10 and the decisions 2 and 7 (small, high risk).
2. P1 items 3 and 5 (audit evidence) before the first production submission.
3. P2 items 13, 14, 15, 12 before a second project goes live.
4. The rest by need: 8 and 9 (submission), 11 (business days), 17 to 20, 23.
