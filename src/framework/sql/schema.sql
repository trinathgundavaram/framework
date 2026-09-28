-- =============================================================================
-- CMS Compliance Framework - schema v6 (single source of truth; design doc §5 describes it)
-- Target: PostgreSQL 14+ (tested on 16). Applied by `framework init-db`, which creates the metadata
-- schema (FRAMEWORK_METADATA_SCHEMA) and sets search_path to it; object names are unqualified.
--
-- DDL standards
--   * Tables are created in dependency order; no ALTER statements, no CHECK constraints (values are
--     validated by the framework and by the loads that populate the configuration tables).
--   * Every constraint and index is named: pk_<table>, fk_<table>_<parent>, uq_<table>[_<what>], ix_<table>_<what>.
--   * Column names follow ComplianceRequestControl: Title_Case words and a class-word suffix
--       _ID identifier   _Cd code     _Nm name        _Desc description  _Ty type    _Stat status
--       _Ind 0/1 flag    _Cnt count   _Dt_Key date    _Dtts timestamptz  _Txt text   _List comma list   _By user
--   * Unquoted identifiers: PostgreSQL stores them lower-case (Req_ID is column req_id).
--   * Tables people maintain carry Created_Dtts/Created_By/Updated_Dtts/Updated_By; tables only the
--     framework writes carry Created_Dtts/Updated_Dtts; append-only logs carry Event_Dtts.
--   * Only indexes that enforce a rule or serve a query the framework runs on a growing table.
--
-- Not stored here: database connections (.env / Secrets Manager), runtime settings (environment /
-- job arguments), report-period SQL (framework/period_sql.py), the event vocabulary (framework/audit.py),
-- the SLA hold (computed as Req_Dt_Key + (SLA_Days - 1)).
-- =============================================================================

-- ================= reference =================
CREATE TABLE ComplianceProject (
  Project_Cd    VARCHAR(30)  NOT NULL,
  Project_Desc  VARCHAR(200) NOT NULL,
  Active_Ind    SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By    VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By    VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_complianceproject PRIMARY KEY (Project_Cd)
);

CREATE TABLE ComplianceSourceSystem (
  Src_ID        VARCHAR(30)  NOT NULL,
  Src_Nm        VARCHAR(100) NOT NULL,
  Src_Ty        VARCHAR(20)  NOT NULL,                     -- VENDOR | INTERNAL
  Active_Ind    SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By    VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By    VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancesourcesystem PRIMARY KEY (Src_ID)
);

CREATE TABLE ComplianceRunType (
  Run_Ty          VARCHAR(20)  NOT NULL,                   -- letters/digits only: it is the {RUNTY} token
  Run_Ty_Desc     VARCHAR(200) NOT NULL,
  Run_Category_Cd VARCHAR(10)  NOT NULL,                   -- ROUTINE (scheduled) | ADHOC (intake)
  SLA_Days        INT          NOT NULL,                   -- >= 1; minimum hold before close (D-38)
  Carry_Fwd_Ind   SMALLINT     NOT NULL DEFAULT 0,         -- REUSE overrides allowed (D-70)
  Active_Ind      SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By      VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By      VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_complianceruntype PRIMARY KEY (Run_Ty)
);

-- ================= configuration =================
-- Which (project, table, source, run type) combinations apply, and when. `validate-config` reports
-- overlapping effective windows.
CREATE TABLE ComplianceDataSetSourceXwalk (
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,            -- physical core table (D-25)
  Src_ID                 VARCHAR(30)  NOT NULL,
  Run_Ty                 VARCHAR(20)  NOT NULL,
  Effective_Start_Dt_Key DATE         NOT NULL,
  Effective_End_Dt_Key   DATE,
  Cmplnc_Vrsn            VARCHAR(10)  NOT NULL,            -- part of Btch_ID (D-51)
  Active_Ind             SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancedatasetsourcexwalk PRIMARY KEY (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key),
  CONSTRAINT fk_compliancedatasetsourcexwalk_project FOREIGN KEY (Project_Cd) REFERENCES ComplianceProject,
  CONSTRAINT fk_compliancedatasetsourcexwalk_source  FOREIGN KEY (Src_ID) REFERENCES ComplianceSourceSystem,
  CONSTRAINT fk_compliancedatasetsourcexwalk_runtype FOREIGN KEY (Run_Ty) REFERENCES ComplianceRunType
);

-- File shape and location per (project, table, source). The filename template spells the project,
-- table and source out literally; the file type is the template's extension. Staging tables live in
-- the framework database; the core table is <Core_Schema_Nm>.<Table_Nm>.
CREATE TABLE ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT       GENERATED ALWAYS AS IDENTITY,
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Src_File_Nm_Tmplt      VARCHAR(255) NOT NULL,            -- §9, e.g. PRJA_TBLX_S1_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt
  Delmtr_Cd              VARCHAR(10),                      -- NULL = comma; TAB | PIPE | COMMA | SEMICOLON | literal
  Src_File_Has_Hdr_Ind   SMALLINT     NOT NULL,
  Src_File_Has_Trlr_Ind  SMALLINT     NOT NULL,
  Allow_Zero_Rcd_Ind     SMALLINT     NOT NULL,            -- D-60
  S3_Src_File_Path       VARCHAR(500) NOT NULL,            -- inbound folder (s3://bucket/prefix/)
  Src_File_Archive_Path  VARCHAR(500) NOT NULL,
  Stg_Schema_Nm          VARCHAR(63)  NOT NULL,
  Stg_Table_Nm           VARCHAR(63)  NOT NULL,
  Core_Schema_Nm         VARCHAR(63)  NOT NULL,
  Sucs_Email_Notfn_Id    TEXT,                             -- comma-separated recipients of AUDIT events
  Failr_Email_Notfn_Id   TEXT,                             -- comma-separated recipients of EXCEPTION events
  Email_Subjct_Txt       VARCHAR(200),                     -- subject prefix
  Active_Ind             SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancesourcefileconfig PRIMARY KEY (Cfg_ID),
  CONSTRAINT fk_compliancesourcefileconfig_project FOREIGN KEY (Project_Cd) REFERENCES ComplianceProject,
  CONSTRAINT fk_compliancesourcefileconfig_source  FOREIGN KEY (Src_ID) REFERENCES ComplianceSourceSystem
);
CREATE UNIQUE INDEX uq_compliancesourcefileconfig_active
  ON ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID) WHERE Active_Ind = 1;

-- GRE rule groups run on each staged file, bound at any level: '*' in Table_Nm, Src_ID or Run_Ty means
-- "all". Additive: every binding that matches the file runs; a rule group/variant bound at several levels
-- runs once. A file with no matching binding skips the rules.
--   project  ('*', '*', '*')   table ('T', '*', '*')   table + run type ('T', '*', 'R')   source ('T', 'S', '*')
CREATE TABLE ComplianceRuleBinding (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,                  -- '*' = every table of the project
  Src_ID           VARCHAR(30)  NOT NULL,                  -- '*' = every source
  Run_Ty           VARCHAR(20)  NOT NULL DEFAULT '*',      -- '*' = every run type
  Gre_Rule_Group   VARCHAR(100) NOT NULL,
  Gre_Rule_Variant VARCHAR(100) NOT NULL,
  Active_Ind       SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancerulebinding PRIMARY KEY (Project_Cd, Table_Nm, Src_ID, Run_Ty, Gre_Rule_Group, Gre_Rule_Variant),
  CONSTRAINT fk_compliancerulebinding_project FOREIGN KEY (Project_Cd) REFERENCES ComplianceProject
);

-- ================= control =================
-- Ad-hoc requests (the run type must be in the ADHOC category). One row asks for batches for the SAME
-- report period on every run date from Req_Start_Dt_Key to Req_End_Dt_Key (equal dates = one-off).
-- The daily sweep handles each request once per run date and records that date in Last_Run_Dt_Key;
-- outcomes (batches created, failures) are in the audit tables under the Intake_ID.
CREATE TABLE ComplianceRequestInTake (
  Intake_ID        BIGINT       GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30),                            -- NULL = every effective source
  Run_Ty           VARCHAR(20)  NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,                  -- report period of every batch created
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Start_Dt_Key DATE         NOT NULL,                  -- first run date
  Req_End_Dt_Key   DATE         NOT NULL,                  -- last run date
  Last_Run_Dt_Key  DATE,                                   -- last run date the sweep handled this row
  Created_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancerequestintake PRIMARY KEY (Intake_ID),
  CONSTRAINT fk_compliancerequestintake_project FOREIGN KEY (Project_Cd) REFERENCES ComplianceProject,
  CONSTRAINT fk_compliancerequestintake_source  FOREIGN KEY (Src_ID) REFERENCES ComplianceSourceSystem,
  CONSTRAINT fk_compliancerequestintake_runtype FOREIGN KEY (Run_Ty) REFERENCES ComplianceRunType
);

-- One row per batch: (project, table, source, run type, report period, run date). Whether it was
-- scheduled or requested follows from Run_Ty's Run_Category_Cd. A batch closes on its own: automatically
-- after its run type's SLA hold (Req_Dt_Key + SLA_Days - 1) when it has data or is in exception, or by a
-- person (`close-batch`). The extract is produced by a separate process.
CREATE TABLE ComplianceRequestControl (
  Req_ID           BIGINT       GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Dt_Key       DATE         NOT NULL,                  -- run date (D-29)
  Btch_ID          VARCHAR(250) NOT NULL,
  Req_Stat         VARCHAR(30)  NOT NULL,                  -- see common.TRANSITIONS
  Resolution_Ty    VARCHAR(15),                            -- NEW_FILE | CARRY_FORWARD | MISSING
  Reuse_Btch_ID    VARCHAR(250),                           -- CARRY_FORWARD: batch whose data is reused
  Batch_Close_Ind  SMALLINT     NOT NULL DEFAULT 0,
  Created_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_compliancerequestcontrol PRIMARY KEY (Req_ID),
  CONSTRAINT uq_compliancerequestcontrol_btch UNIQUE (Btch_ID),
  CONSTRAINT uq_compliancerequestcontrol UNIQUE (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key),
  CONSTRAINT fk_compliancerequestcontrol_project FOREIGN KEY (Project_Cd) REFERENCES ComplianceProject,
  CONSTRAINT fk_compliancerequestcontrol_source  FOREIGN KEY (Src_ID) REFERENCES ComplianceSourceSystem,
  CONSTRAINT fk_compliancerequestcontrol_runtype FOREIGN KEY (Run_Ty) REFERENCES ComplianceRunType
);

-- One row per S3 object, including quarantined files. The batch's current data is its row with
-- Load_Stat = 'PROMOTED'. Report period and run type come from the batch (Req_ID).
CREATE TABLE ComplianceFileLoad (
  Load_ID           BIGINT        GENERATED ALWAYS AS IDENTITY,
  S3_Bucket         VARCHAR(100)  NOT NULL,
  S3_Key            VARCHAR(1024) NOT NULL,
  S3_Version_Id     VARCHAR(200),
  S3_ETag           VARCHAR(200)  NOT NULL,
  File_Size_Byte    BIGINT        NOT NULL,
  File_Sha256       CHAR(64),
  Cfg_ID            BIGINT,
  Req_ID            BIGINT,
  Btch_ID           VARCHAR(250),
  Load_Stat         VARCHAR(25)   NOT NULL,                -- RECEIVED | STAGING | STAGED | RULES_RUNNING | PROMOTED |
                                                           -- SUPERSEDED | RULES_FAILED | QUARANTINED | FAILED_TECHNICAL
  Rules_Stat        VARCHAR(25)   NOT NULL DEFAULT 'NOT_RUN',
  Quarantine_Rsn_Cd VARCHAR(60),                           -- the quarantine event code
  Stg_Rcd_Cnt       BIGINT,
  Error_Txt         TEXT,
  Created_Dtts      TIMESTAMPTZ   NOT NULL DEFAULT now(),  -- received
  Updated_Dtts      TIMESTAMPTZ   NOT NULL DEFAULT now(),  -- last progress (stale-load health check)
  CONSTRAINT pk_compliancefileload PRIMARY KEY (Load_ID),
  CONSTRAINT fk_compliancefileload_config FOREIGN KEY (Cfg_ID) REFERENCES ComplianceSourceFileConfig,
  CONSTRAINT fk_compliancefileload_batch  FOREIGN KEY (Req_ID) REFERENCES ComplianceRequestControl
);
CREATE UNIQUE INDEX uq_compliancefileload_object   ON ComplianceFileLoad (S3_Bucket, S3_Key, COALESCE(S3_Version_Id, S3_ETag));
CREATE UNIQUE INDEX uq_compliancefileload_promoted ON ComplianceFileLoad (Btch_ID) WHERE Load_Stat = 'PROMOTED';
CREATE INDEX ix_compliancefileload_sha ON ComplianceFileLoad (File_Sha256);

-- Manual decisions, one shape for three cases (D-70, D-74):
--   REUSE         an open batch without data reuses an earlier batch's data (run type Carry_Fwd_Ind = 1)
--   LATE_ARRIVAL  a file may still be promoted into a closed batch that has no data
--   CORRECTION    a corrected file may replace the data of a closed batch
-- Created_By requests, Reviewed_By approves or rejects. An approval is valid through Valid_Thru_Dt_Key.
CREATE TABLE ComplianceBatchOverride (
  Ovrd_ID           BIGINT       GENERATED ALWAYS AS IDENTITY,
  Override_Ty       VARCHAR(20)  NOT NULL,
  Req_ID            BIGINT       NOT NULL,
  Btch_ID           VARCHAR(250) NOT NULL,
  Reuse_Btch_ID     VARCHAR(250),                          -- REUSE: optional; else the latest is used
  Apprvl_Stat       VARCHAR(20)  NOT NULL DEFAULT 'PENDING_REVIEW',  -- PENDING_REVIEW | APPROVED | REJECTED
  Valid_Thru_Dt_Key DATE,                                  -- required once APPROVED
  Rsn_Txt           TEXT,
  Reviewed_By       VARCHAR(100),
  Reviewed_Dtts     TIMESTAMPTZ,
  Created_Dtts      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By        VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By        VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancebatchoverride PRIMARY KEY (Ovrd_ID),
  CONSTRAINT fk_compliancebatchoverride_batch FOREIGN KEY (Req_ID) REFERENCES ComplianceRequestControl
);
CREATE UNIQUE INDEX uq_compliancebatchoverride_active
  ON ComplianceBatchOverride (Req_ID, Override_Ty) WHERE Apprvl_Stat IN ('PENDING_REVIEW', 'APPROVED');

-- ================= audit (append-only; written only through audit.EventLogger) =================
-- Batch timeline.
CREATE TABLE ComplianceRequestFileDetail (
  Detail_ID   BIGINT       GENERATED ALWAYS AS IDENTITY,
  Req_ID      BIGINT       NOT NULL,
  Btch_ID     VARCHAR(250) NOT NULL,
  Load_ID     BIGINT,
  Ovrd_ID     BIGINT,
  Intake_ID   BIGINT,
  Event_Ty    VARCHAR(60)  NOT NULL,
  Actor       VARCHAR(100) NOT NULL,
  Event_Txt   TEXT,
  Event_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_compliancerequestfiledetail PRIMARY KEY (Detail_ID),
  CONSTRAINT fk_compliancerequestfiledetail_batch    FOREIGN KEY (Req_ID) REFERENCES ComplianceRequestControl,
  CONSTRAINT fk_compliancerequestfiledetail_load     FOREIGN KEY (Load_ID) REFERENCES ComplianceFileLoad,
  CONSTRAINT fk_compliancerequestfiledetail_override FOREIGN KEY (Ovrd_ID) REFERENCES ComplianceBatchOverride,
  CONSTRAINT fk_compliancerequestfiledetail_intake   FOREIGN KEY (Intake_ID) REFERENCES ComplianceRequestInTake
);

-- Exceptions and notable events; Notified_Ind = 0 rows are waiting to be emailed.
CREATE TABLE CMS_ComplianceExceptionsAudit (
  Event_ID     BIGINT        GENERATED ALWAYS AS IDENTITY,
  Event_Ty     VARCHAR(60)   NOT NULL,
  Sevrty       VARCHAR(10)   NOT NULL,                     -- INFO | WARNING | ERROR
  Project_Cd   VARCHAR(30),
  Table_Nm     VARCHAR(63),
  Src_ID       VARCHAR(30),
  Run_Ty       VARCHAR(20),
  Req_ID       BIGINT,
  Load_ID      BIGINT,
  Ovrd_ID      BIGINT,
  Intake_ID    BIGINT,
  Btch_ID      VARCHAR(250),
  File_Ref     VARCHAR(1100),
  Actor        VARCHAR(100)  NOT NULL,
  Event_Txt    TEXT,
  Event_Dtts   TIMESTAMPTZ   NOT NULL DEFAULT now(),
  Notified_Ind SMALLINT      NOT NULL DEFAULT 0,
  CONSTRAINT pk_cms_complianceexceptionsaudit PRIMARY KEY (Event_ID),
  CONSTRAINT fk_cms_complianceexceptionsaudit_batch    FOREIGN KEY (Req_ID) REFERENCES ComplianceRequestControl,
  CONSTRAINT fk_cms_complianceexceptionsaudit_load     FOREIGN KEY (Load_ID) REFERENCES ComplianceFileLoad,
  CONSTRAINT fk_cms_complianceexceptionsaudit_override FOREIGN KEY (Ovrd_ID) REFERENCES ComplianceBatchOverride,
  CONSTRAINT fk_cms_complianceexceptionsaudit_intake   FOREIGN KEY (Intake_ID) REFERENCES ComplianceRequestInTake
);
CREATE INDEX ix_cms_complianceexceptionsaudit_unsent ON CMS_ComplianceExceptionsAudit (Event_ID) WHERE Notified_Ind = 0;

-- ================= target-table framework columns (per staging / core table) =================
-- ALTER TABLE <stg>  ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Src_File_Nm VARCHAR(1024) NOT NULL, ADD COLUMN Stg_Load_Dtts TIMESTAMPTZ NOT NULL;
-- ALTER TABLE <core> ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Current_Ind SMALLINT NOT NULL, ADD COLUMN Load_Dtts TIMESTAMPTZ NOT NULL,
--                    ADD COLUMN End_Dtts TIMESTAMPTZ;
-- CREATE INDEX ON <stg> (Btch_ID); CREATE INDEX ON <core> (Btch_ID) WHERE Current_Ind = 1;
