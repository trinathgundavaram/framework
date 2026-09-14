# framework

Reusable, metadata-driven CMS compliance data framework (Program Audit
ODAG/CDAG, Part C ODR, Part C SLIDA, etc.) on AWS.

## Contents

- [`docs/design/framework-master.md`](docs/design/framework-master.md) —
  **current, authoritative design.** Consolidated requirements, final
  DDLs for all five tables, the full file-to-extract pipeline end to end,
  and a dedicated review of failure points found across every scenario
  (three real gaps identified and closed: a file loaded while an anchor
  is active must never feed the extract, a second correction to an
  already-reopened date had no path, and an approved anchor could be
  created with no real expiry — now a hard `CHECK` constraint).
- [`docs/design/orchestration-flow.md`](docs/design/orchestration-flow.md) —
  **process view.** Daily orchestration sequence, the full per-file
  decision flowchart, the Override row's state machine, and the AWS
  service mapping (EventBridge / Lambda / Step Functions / Glue / RDS /
  SNS) for every step.
- [`docs/design/reuse-and-late-arrival.md`](docs/design/reuse-and-late-arrival.md) —
  earlier draft; superseded by the master doc above, kept for the two
  worked examples (still accurate) and history.
- [`docs/design/schema-design.md`](docs/design/schema-design.md) — earliest
  draft; superseded, kept for history.
- [`mock-data/cms_compliance_framework_tables.xlsx`](mock-data/cms_compliance_framework_tables.xlsx) —
  reference tables plus `ComplianceRequestControl`, `ComplianceBatchOverride`,
  `ComplianceExtractControl`, and `CMS_ComplianceExceptionsAudit`, matching
  the master doc's schema and worked examples exactly, including the
  `Received_File_Ref` and `Reuse_Valid_Thru_Dt_Key` fixes.
