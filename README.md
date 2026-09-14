# framework

Reusable, metadata-driven CMS compliance data framework (Program Audit
ODAG/CDAG, Part C ODR, Part C SLIDA, etc.) on AWS.

## Contents

- [`docs/design/reuse-and-late-arrival.md`](docs/design/reuse-and-late-arrival.md) —
  **current design.** DDLs for all five tables, plus how carry-forward
  reuse, batch close, late-arrival reopen, correction reopen, and
  upstream file rejection work, with two fully worked examples.
- [`docs/design/schema-design.md`](docs/design/schema-design.md) — earlier
  version of the schema design; superseded by the doc above, kept for
  history.
- [`mock-data/cms_compliance_framework_tables.xlsx`](mock-data/cms_compliance_framework_tables.xlsx) —
  reference tables plus `ComplianceRequestControl`, `ComplianceBatchOverride`,
  `ComplianceExtractControl`, and `CMS_ComplianceExceptionsAudit`, populated
  with the two worked examples from the design doc (5-day carry-forward
  table, 5-day/3-FDR reopen-and-rejection table).
