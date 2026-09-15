-- =============================================================================
-- CMS Compliance Framework — Supporting Metadata Tables (PostgreSQL DDL)
-- =============================================================================
-- Scope: the five reference / ad-hoc / audit tables identified in
-- docs/design/schema-design.md as "one-time seed or ad-hoc updates":
--   1. compliance_source_system        (one-time seed, rarely changes)
--   2. compliance_run_type             (one-time seed, rarely changes)
--   3. compliance_dataset_source_xwalk (one-time seed + occasional manual edits)
--   4. compliance_request_intake       (human-inserted ad-hoc, updated as processed)
--   5. cms_compliance_exceptions_audit (append-only narrative log)
--
-- These are NOT the core pipeline tables (ComplianceRequestControl,
-- ComplianceBatchOverride, ComplianceRequestFileDetail, ComplianceExtractControl) —
-- those are written by the orchestration pipeline itself, not by this loader.
--
-- Safe to run repeatedly (CREATE TABLE IF NOT EXISTS).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS cms_compliance;

-- -----------------------------------------------------------------------------
-- 1. compliance_source_system — source system master
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cms_compliance.compliance_source_system (
    src_cd        VARCHAR(10)  NOT NULL,
    src_nm        VARCHAR(100) NOT NULL,
    src_ty        VARCHAR(20)  NOT NULL,       -- e.g. VENDOR
    active_ind    CHAR(1)      NOT NULL DEFAULT 'Y',
    created_dtts  TIMESTAMP    NOT NULL DEFAULT now(),
    updated_dtts  TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT pk_compliance_source_system PRIMARY KEY (src_cd)
);

-- -----------------------------------------------------------------------------
-- 2. compliance_run_type — run type master
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cms_compliance.compliance_run_type (
    run_ty        VARCHAR(20)  NOT NULL,       -- CMS / DAILY
    run_ty_desc   VARCHAR(200) NOT NULL,
    sla_days      SMALLINT     NOT NULL,
    created_dtts  TIMESTAMP    NOT NULL DEFAULT now(),
    updated_dtts  TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT pk_compliance_run_type PRIMARY KEY (run_ty)
);

-- -----------------------------------------------------------------------------
-- 3. compliance_dataset_source_xwalk — carry-forward eligibility + config
--    per (project, table, source, run type, compliance version)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cms_compliance.compliance_dataset_source_xwalk (
    project_cd          VARCHAR(30) NOT NULL,
    table_nm             VARCHAR(50) NOT NULL,
    src_cd               VARCHAR(10) NOT NULL,
    run_ty                VARCHAR(20) NOT NULL,
    cmplnc_vrsn           VARCHAR(10) NOT NULL,
    carry_fwd_elig_ind    CHAR(1)     NOT NULL DEFAULT 'N',
    created_dtts          TIMESTAMP   NOT NULL DEFAULT now(),
    updated_dtts          TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT pk_compliance_dataset_source_xwalk
        PRIMARY KEY (project_cd, table_nm, src_cd, run_ty, cmplnc_vrsn),
    CONSTRAINT fk_xwalk_src_cd FOREIGN KEY (src_cd)
        REFERENCES cms_compliance.compliance_source_system (src_cd),
    CONSTRAINT fk_xwalk_run_ty FOREIGN KEY (run_ty)
        REFERENCES cms_compliance.compliance_run_type (run_ty)
);

-- -----------------------------------------------------------------------------
-- 4. compliance_request_intake — the one table humans write to directly
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cms_compliance.compliance_request_intake (
    intake_id       VARCHAR(50) NOT NULL,
    project_cd      VARCHAR(30) NOT NULL,
    table_nm        VARCHAR(50) NOT NULL,
    src_cd          VARCHAR(10),
    req_dt_key      DATE,
    req_ty          VARCHAR(30) NOT NULL,        -- CORRECTION_REQUEST / CYCLE_INIT / ADHOC_PULL
    requested_by    VARCHAR(50) NOT NULL,
    requested_dtts  TIMESTAMP   NOT NULL,
    rsn             TEXT,
    processed_ind   CHAR(1)     NOT NULL DEFAULT 'N',
    processed_dtts  TIMESTAMP,
    created_dtts    TIMESTAMP   NOT NULL DEFAULT now(),
    updated_dtts    TIMESTAMP   NOT NULL DEFAULT now(),
    CONSTRAINT pk_compliance_request_intake PRIMARY KEY (intake_id)
);

-- -----------------------------------------------------------------------------
-- 5. cms_compliance_exceptions_audit — append-only narrative log
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cms_compliance.cms_compliance_exceptions_audit (
    event_id     BIGINT       NOT NULL,
    btch_id      VARCHAR(100),                   -- nullable: some exceptions have no batch yet
    event_ctgy   VARCHAR(20)  NOT NULL,           -- EXCEPTION / AUDIT
    event_ty     VARCHAR(60)  NOT NULL,
    sevrty       VARCHAR(10)  NOT NULL,           -- INFO / WARNING / ERROR
    actor        VARCHAR(50)  NOT NULL,           -- SYSTEM or username
    event_dtts   TIMESTAMP    NOT NULL,
    description  TEXT,
    loaded_dtts  TIMESTAMP    NOT NULL DEFAULT now(),
    CONSTRAINT pk_cms_compliance_exceptions_audit PRIMARY KEY (event_id)
);

CREATE INDEX IF NOT EXISTS ix_exceptions_audit_btch_id
    ON cms_compliance.cms_compliance_exceptions_audit (btch_id);
