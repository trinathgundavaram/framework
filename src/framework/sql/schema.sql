-- =============================================================================
-- CMS Compliance Framework - schema (single source of truth; design doc §5 describes it)
-- Target: PostgreSQL 14+ (tested on 16). Requires the btree_gist extension.
-- Applied by `framework init-db`, which creates the metadata schema (FRAMEWORK_METADATA_SCHEMA),
-- sets search_path to it and installs btree_gist first. Object names are intentionally unqualified.
-- Tables are created in dependency order; there are no ALTER statements.
--
-- Not stored here (v4/v5 simplification):
--   * database connections  -> .env file locally, AWS Secrets Manager in AWS
--   * runtime settings      -> environment / .env / job arguments
--   * report-period logic   -> framework/period_sql.py (name passed when the job is scheduled)
--   * extract job           -> the scheduled job chain runs the extract after the framework closes it
--   * Req_Stat values       -> fixed CHECK list below (transitions enforced in code)
--   * SLA hold              -> computed as Req_Dt_Key + (SLA_Days - 1); never stored
-- =============================================================================
-- ================= lookups =================
CREATE TABLE ComplianceSourceSystem (
  Src_Cd       VARCHAR(30)  PRIMARY KEY,
  Src_Nm       VARCHAR(100) NOT NULL,
  Src_Ty       VARCHAR(20)  NOT NULL CHECK (Src_Ty IN ('VENDOR','INTERNAL')),
  Active_Ind   SMALLINT     NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user
);

CREATE TABLE ComplianceRunType (
  Run_Ty          VARCHAR(20)  PRIMARY KEY CHECK (Run_Ty ~ '^[A-Za-z0-9]+$'),   -- must be usable as {RUNTY}
  Run_Ty_Desc     VARCHAR(200) NOT NULL,
  Run_Category_Cd VARCHAR(10)  NOT NULL CHECK (Run_Category_Cd IN ('ROUTINE','ADHOC')),
  SLA_Days        INT          NOT NULL CHECK (SLA_Days >= 1),                  -- D-38: minimum hold before close
  Carry_Fwd_Ind   SMALLINT     NOT NULL DEFAULT 0 CHECK (Carry_Fwd_Ind IN (0,1)),  -- D-70: REUSE overrides allowed
  Active_Ind      SMALLINT     NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user
);

CREATE TABLE ComplianceEventType (
  Event_Ty          VARCHAR(60) PRIMARY KEY,
  Log_Tbl_Cd        VARCHAR(20) NOT NULL CHECK (Log_Tbl_Cd IN ('FILE_DETAIL','EXCEPTIONS_AUDIT')),
  Event_Ctgy        VARCHAR(20) NOT NULL CHECK (Event_Ctgy IN ('AUDIT','EXCEPTION')),
  Default_Sevrty    VARCHAR(10) NOT NULL CHECK (Default_Sevrty IN ('INFO','WARNING','ERROR')),
  Notify_Ind        SMALLINT    NOT NULL DEFAULT 0 CHECK (Notify_Ind IN (0,1)),
  Notify_Channel_Cd VARCHAR(10) NOT NULL DEFAULT 'SES' CHECK (Notify_Channel_Cd IN ('SES','SNS','BOTH')),
  Event_Desc        VARCHAR(300)
);

-- ================= configuration =================
-- Which (project, table, source, run type) combinations apply, and when. Nothing else.
CREATE TABLE ComplianceDataSetSourceXwalk (
  Project_Cd         VARCHAR(30) NOT NULL,
  Table_Nm           VARCHAR(63) NOT NULL,                              -- physical core table (D-25)
  Src_Cd             VARCHAR(30) NOT NULL REFERENCES ComplianceSourceSystem,
  Run_Ty             VARCHAR(20) NOT NULL REFERENCES ComplianceRunType,
  Effective_Start_Dt DATE        NOT NULL,
  Effective_End_Dt   DATE,
  Cmplnc_Vrsn        VARCHAR(10) NOT NULL,                              -- D-51
  Active_Ind         SMALLINT    NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user,
  PRIMARY KEY (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Effective_Start_Dt),
  CHECK (Effective_End_Dt IS NULL OR Effective_End_Dt >= Effective_Start_Dt),
  CONSTRAINT ex_xwalk_no_overlap EXCLUDE USING gist (
    Project_Cd WITH =, Table_Nm WITH =, Src_Cd WITH =, Run_Ty WITH =,
    daterange(Effective_Start_Dt, Effective_End_Dt, '[]') WITH &&) WHERE (Active_Ind = 1)
);

-- File shape and location per (project, table, source). Staging/core tables live in the same
-- PostgreSQL database as the framework tables (schema-qualified here).
CREATE TABLE ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_Cd                 VARCHAR(30)  NOT NULL REFERENCES ComplianceSourceSystem,
  Src_File_Nm_Tmplt      VARCHAR(255) NOT NULL,                         -- §9 (validator checks grammar)
  Project_Alias          VARCHAR(50)  NOT NULL,
  Table_Alias            VARCHAR(80)  NOT NULL,
  Src_Alias              VARCHAR(50)  NOT NULL,
  Src_File_Ty            VARCHAR(10)  NOT NULL,                         -- allowed set: SUPPORTED_FILE_TYPES
  Delmtr_Cd              VARCHAR(5),
  Line_Term_Cd           VARCHAR(5),
  Src_File_Has_Hdr_Ind   SMALLINT NOT NULL CHECK (Src_File_Has_Hdr_Ind IN (0,1)),
  Src_File_Has_Trlr_Ind  SMALLINT NOT NULL CHECK (Src_File_Has_Trlr_Ind IN (0,1)),
  Allow_Zero_Rcd_Ind     SMALLINT NOT NULL CHECK (Allow_Zero_Rcd_Ind IN (0,1)),         -- D-60
  S3_Src_File_Path       VARCHAR(500) NOT NULL,
  Src_File_Archive_Path  VARCHAR(500) NOT NULL,
  S3_Quarantine_Path     VARCHAR(500) NOT NULL,
  Stg_Schema_Nm          VARCHAR(63)  NOT NULL,
  Stg_Tblnm              VARCHAR(63)  NOT NULL,
  Core_Schema_Nm         VARCHAR(63)  NOT NULL,
  Core_Tblnm             VARCHAR(63)  NOT NULL,
  Bus_Email_Id           VARCHAR(500),
  Bus_Usr_Grp_Nm         VARCHAR(100),
  Dlvry_Ownr_Grp_Nm      VARCHAR(100),
  Sucs_Email_Notfn_Id    TEXT,
  Failr_Email_Notfn_Id   TEXT,
  Email_Subjct_Txt       TEXT,
  Email_Cntnt_Txt        TEXT,
  Notify_Channel_Cd      VARCHAR(10) CHECK (Notify_Channel_Cd IN ('SES','SNS','BOTH')),  -- overrides event default;
                                                                                         -- SNS topic = FRAMEWORK_SNS_TOPIC_ARN
  Active_Ind             SMALLINT NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Created_By VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_By VARCHAR(100) NOT NULL DEFAULT current_user,
  CHECK (Core_Tblnm = Table_Nm)
);
CREATE UNIQUE INDEX ux_filecfg_one_active ON ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_Cd) WHERE Active_Ind = 1;
CREATE UNIQUE INDEX ux_filecfg_alias_active ON ComplianceSourceFileConfig (Project_Alias, Table_Alias, Src_Alias) WHERE Active_Ind = 1;

-- GRE rule groups. FILE_LEVEL rules run when at least one binding exists for the source.
CREATE TABLE ComplianceRuleBinding (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_Cd           VARCHAR(30)  NOT NULL,                               -- '*' for PERIOD_LEVEL
  Rule_Scope_Cd    VARCHAR(20)  NOT NULL CHECK (Rule_Scope_Cd IN ('FILE_LEVEL','PERIOD_LEVEL')),
  Gre_Rule_Group   VARCHAR(100) NOT NULL,
  Gre_Rule_Variant VARCHAR(100) NOT NULL,
  Active_Ind       SMALLINT NOT NULL DEFAULT 1 CHECK (Active_Ind IN (0,1)),
  PRIMARY KEY (Project_Cd, Table_Nm, Src_Cd, Rule_Scope_Cd, Gre_Rule_Group, Gre_Rule_Variant),
  CHECK ((Rule_Scope_Cd = 'PERIOD_LEVEL') = (Src_Cd = '*'))
);

-- ================= control =================
-- Ad-hoc requests only (the run type must be in the ADHOC category). One row asks for batches to be
-- created for the SAME report period on every run date from Req_Start_Dt_Key to Req_End_Dt_Key
-- (a one-off request has the same start and end date).
CREATE TABLE ComplianceRequestInTake (
  Intake_ID        VARCHAR(50)  PRIMARY KEY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL REFERENCES ComplianceRunType,
  Src_Cd           VARCHAR(30)  REFERENCES ComplianceSourceSystem,      -- NULL = every effective source
  Req_Ty           VARCHAR(30)  NOT NULL,                               -- project's own ad-hoc request type
  Rpt_Start_Dt_Key DATE         NOT NULL,                               -- report period of every batch created
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Start_Dt_Key DATE         NOT NULL,                               -- first run date batches are created for
  Req_End_Dt_Key   DATE         NOT NULL,                               -- last run date (same value = one-off)
  Rsn              TEXT,
  Requested_By     VARCHAR(100) NOT NULL,
  Requested_Dtts   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Intake_Stat      VARCHAR(20)  NOT NULL DEFAULT 'NEW'
                   CHECK (Intake_Stat IN ('NEW','IN_PROGRESS','COMPLETED','FAILED')),
  Last_Created_Dt_Key DATE,                                             -- last run date batches were created for
  Processed_Dtts   TIMESTAMPTZ,
  Error_Txt        TEXT,
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Req_End_Dt_Key >= Req_Start_Dt_Key)
);
CREATE INDEX ix_intake_open ON ComplianceRequestInTake (Req_Start_Dt_Key) WHERE Intake_Stat IN ('NEW','IN_PROGRESS');

-- One row per (project, table, run type, report period, run date): everything the framework must
-- collect before that run's extract may be generated.
CREATE TABLE ComplianceExtractControl (
  Extract_ID                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd                VARCHAR(30) NOT NULL,
  Table_Nm                  VARCHAR(63) NOT NULL,
  Run_Ty                    VARCHAR(20) NOT NULL REFERENCES ComplianceRunType,
  Rpt_Start_Dt_Key          DATE NOT NULL,
  Rpt_End_Dt_Key            DATE NOT NULL,
  Req_Dt_Key                DATE NOT NULL,                              -- run date; with SLA_Days gives the hold
  Required_Src_Cnt          INT  NOT NULL CHECK (Required_Src_Cnt >= 0),
  Received_Src_Cnt          INT  NOT NULL DEFAULT 0,
  Included_Src_Cds TEXT, Missing_Src_Cds TEXT,
  Carried_Src_Cds           TEXT,          -- sources counted as received through an approved REUSE (D-70)
  Extract_Stat              VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (Extract_Stat IN ('PENDING','PARTIAL','COMPLETE')),
  Extract_Rules_Stat        VARCHAR(25) NOT NULL DEFAULT 'PENDING'
                            CHECK (Extract_Rules_Stat IN ('PENDING','PASSED','PASSED_WITH_WARNINGS','FAILED','ERROR')),
  Failed_Rule_Refs          TEXT,          -- GATE-failed period rules of the last combine
  Eligibility_Cd            VARCHAR(15) NOT NULL DEFAULT 'NOT_ELIGIBLE'
                            CHECK (Eligibility_Cd IN ('NOT_ELIGIBLE','MANUAL_ONLY','AUTO')),
  Eligibility_Rsn_Txt       VARCHAR(1000),
  Extract_Close_Ind         SMALLINT NOT NULL DEFAULT 0 CHECK (Extract_Close_Ind IN (0,1)),
  Extract_Closed_Dtts       TIMESTAMPTZ,
  Closed_By                 VARCHAR(100),
  Close_Warning_Txt         TEXT,
  Regenerate_Required_Ind   SMALLINT NOT NULL DEFAULT 0 CHECK (Regenerate_Required_Ind IN (0,1)),  -- data changed after close
  Combine_Run_Cnt           INT NOT NULL DEFAULT 0,
  Combine_Last_Run_Dtts     TIMESTAMPTZ,
  Combine_Last_Trigger_Cd   VARCHAR(20) CHECK (Combine_Last_Trigger_Cd IN
                              ('EARLY_COMPLETE','SLA_EVALUATION','MANUAL_CLOSE','LATE_PROMOTION',
                               'OVERRIDE_DECISION','MANUAL_REFRESH')),
  Combine_Btch_ID_List      TEXT,
  Data_Signature            CHAR(64),      -- hash of the (Btch_ID, promoted Load_ID) pairs at the last combine
  Closed_Data_Signature     CHAR(64),      -- Data_Signature when the extract was closed
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key),
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Extract_Close_Ind = 0 OR (Extract_Closed_Dtts IS NOT NULL AND Closed_By IS NOT NULL))
);
CREATE INDEX ix_extract_open ON ComplianceExtractControl (Req_Dt_Key) WHERE Extract_Close_Ind = 0;

-- One row per batch: (project, table, source, run type, report period, run date).
CREATE TABLE ComplianceRequestControl (
  Req_ID               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd           VARCHAR(30)  NOT NULL,
  Table_Nm             VARCHAR(63)  NOT NULL,
  Src_Cd               VARCHAR(30)  NOT NULL REFERENCES ComplianceSourceSystem,
  Run_Ty               VARCHAR(20)  NOT NULL REFERENCES ComplianceRunType,
  Rpt_Start_Dt_Key     DATE         NOT NULL,
  Rpt_End_Dt_Key       DATE         NOT NULL,
  Req_Dt_Key           DATE         NOT NULL,                           -- run date the batch was created for (D-29)
  Btch_ID              VARCHAR(250) NOT NULL UNIQUE,
  Cmplnc_Vrsn          VARCHAR(10)  NOT NULL,
  Extract_ID           BIGINT       NOT NULL REFERENCES ComplianceExtractControl,
  Req_Stat             VARCHAR(30)  NOT NULL CHECK (Req_Stat IN ('PENDING','PROMOTED','CARRIED_FORWARD',
                         'EXCEPTION_PENDING','COMPLETED','COMPLETED_WITH_EXCEPTION','DATA_NOT_PROVIDED')),
  Resolution_Ty        VARCHAR(15)  CHECK (Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD','MISSING')),
  Reuse_Btch_ID        VARCHAR(250),                                    -- CARRY_FORWARD: batch whose data is reused
  Batch_Close_Ind      SMALLINT     NOT NULL DEFAULT 0 CHECK (Batch_Close_Ind IN (0,1)),
  Created_By           VARCHAR(15)  NOT NULL CHECK (Created_By IN ('SCHEDULER','ADHOC_INTAKE')),
  Created_Dtts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_Dtts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key),   -- D-30
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK ((Resolution_Ty = 'CARRY_FORWARD') = (Reuse_Btch_ID IS NOT NULL)),
  CHECK (Batch_Close_Ind = 0 OR Resolution_Ty IS NOT NULL)
);
CREATE INDEX ix_crc_extract ON ComplianceRequestControl (Extract_ID);
CREATE INDEX ix_crc_open    ON ComplianceRequestControl (Extract_ID) WHERE Batch_Close_Ind = 0;

-- One row per physical S3 object, including quarantined files. The batch's current data is the
-- row with Load_Stat = 'PROMOTED' (promotion supersedes the previous one).
CREATE TABLE ComplianceFileLoad (
  Load_ID                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  S3_Bucket               VARCHAR(100)  NOT NULL,
  S3_Key                  VARCHAR(1024) NOT NULL,
  S3_Version_Id           VARCHAR(200),
  S3_ETag                 VARCHAR(200)  NOT NULL,
  File_Size_Byte          BIGINT        NOT NULL,
  File_Sha256             CHAR(64),
  Received_Dtts           TIMESTAMPTZ   NOT NULL DEFAULT now(),
  Parsed_Project_Alias    VARCHAR(50), Parsed_Table_Alias VARCHAR(80), Parsed_Src_Alias VARCHAR(50),
  Parsed_Run_Ty           VARCHAR(20), Parsed_Rpt_Start_Dt_Key DATE, Parsed_Rpt_End_Dt_Key DATE,
  Parsed_File_Ts          TIMESTAMP,
  Cfg_ID                  BIGINT REFERENCES ComplianceSourceFileConfig,
  Req_ID                  BIGINT REFERENCES ComplianceRequestControl,
  Btch_ID                 VARCHAR(250),
  Load_Stat               VARCHAR(25) NOT NULL CHECK (Load_Stat IN ('RECEIVED','QUARANTINED','STAGING','STAGED',
                            'RULES_RUNNING','RULES_FAILED','PROMOTED','SUPERSEDED','FAILED_TECHNICAL')),
  Rules_Stat              VARCHAR(25) NOT NULL DEFAULT 'NOT_RUN'
                          CHECK (Rules_Stat IN ('NOT_RUN','PASSED','PASSED_WITH_WARNINGS','FAILED','ERROR')),
  Quarantine_Rsn_Cd       VARCHAR(60) REFERENCES ComplianceEventType,
  Src_Rcd_Cnt BIGINT, Trlr_Rcd_Cnt BIGINT, Stg_Rcd_Cnt BIGINT, Core_Appended_Cnt BIGINT, Core_Disabled_Cnt BIGINT,
  Attempt_Cnt             INT NOT NULL DEFAULT 0,
  Heartbeat_Dtts          TIMESTAMPTZ,
  Promoted_Dtts           TIMESTAMPTZ,
  Error_Txt               TEXT,
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (Load_Stat <> 'QUARANTINED' OR Quarantine_Rsn_Cd IS NOT NULL),
  CHECK (Load_Stat IN ('RECEIVED','QUARANTINED') OR (Req_ID IS NOT NULL AND Btch_ID IS NOT NULL))
);
CREATE UNIQUE INDEX ux_fileload_s3obj ON ComplianceFileLoad (S3_Bucket, S3_Key, COALESCE(S3_Version_Id, S3_ETag));
CREATE UNIQUE INDEX ux_fileload_promoted ON ComplianceFileLoad (Btch_ID) WHERE Load_Stat = 'PROMOTED';
CREATE INDEX ix_fileload_btch ON ComplianceFileLoad (Btch_ID, Load_Stat);
CREATE INDEX ix_fileload_sha  ON ComplianceFileLoad (File_Sha256);

-- Manual decisions, one shape for all three cases (D-70, D-74):
--   REUSE         an open batch without data reuses an earlier batch's data (run type Carry_Fwd_Ind = 1)
--   LATE_ARRIVAL  a file may still be promoted into a closed batch that has no data
--   CORRECTION    a corrected file may replace the data of a closed batch
-- An approval is valid through Valid_Thru_Dt_Key; to stop it early, set that date in the past.
CREATE TABLE ComplianceBatchOverride (
  Ovrd_ID              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Override_Ty          VARCHAR(20) NOT NULL CHECK (Override_Ty IN ('REUSE','LATE_ARRIVAL','CORRECTION')),
  Req_ID               BIGINT NOT NULL REFERENCES ComplianceRequestControl,
  Project_Cd           VARCHAR(30) NOT NULL,
  Table_Nm             VARCHAR(63) NOT NULL,
  Src_Cd               VARCHAR(30) NOT NULL,
  Run_Ty               VARCHAR(20) NOT NULL,
  Rpt_Start_Dt_Key     DATE NOT NULL,
  Rpt_End_Dt_Key       DATE NOT NULL,
  Req_Dt_Key           DATE NOT NULL,
  Btch_ID              VARCHAR(250) NOT NULL,
  Reuse_Btch_ID        VARCHAR(250),                                    -- REUSE: optional; else the latest is used
  Apprvl_Stat          VARCHAR(20) NOT NULL DEFAULT 'PENDING_REVIEW'
                       CHECK (Apprvl_Stat IN ('PENDING_REVIEW','APPROVED','REJECTED')),
  Valid_Thru_Dt_Key    DATE,                                            -- required once APPROVED
  Rsn                  TEXT,
  Requested_By         VARCHAR(100) NOT NULL DEFAULT current_user,
  Apprvd_By  VARCHAR(100), Apprvd_Dtts  TIMESTAMPTZ,
  Rejected_By VARCHAR(100), Rejected_Dtts TIMESTAMPTZ,
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  Updated_By   VARCHAR(100) NOT NULL DEFAULT current_user,
  CHECK (Apprvl_Stat <> 'APPROVED' OR (Apprvd_By IS NOT NULL AND Valid_Thru_Dt_Key IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'REJECTED' OR Rejected_By IS NOT NULL),
  CHECK (Override_Ty <> 'REUSE' OR Reuse_Btch_ID IS DISTINCT FROM Btch_ID)
);
CREATE UNIQUE INDEX ux_ovrd_active ON ComplianceBatchOverride (Req_ID, Override_Ty)
  WHERE Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE INDEX ix_ovrd_pending ON ComplianceBatchOverride (Ovrd_ID) WHERE Apprvl_Stat = 'PENDING_REVIEW';

-- ================= audit =================
CREATE TABLE ComplianceRequestFileDetail (
  Detail_ID         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Req_ID            BIGINT NOT NULL REFERENCES ComplianceRequestControl,
  Btch_ID           VARCHAR(250) NOT NULL,
  Load_ID           BIGINT REFERENCES ComplianceFileLoad,
  Ovrd_ID           BIGINT REFERENCES ComplianceBatchOverride,
  Intake_ID         VARCHAR(50) REFERENCES ComplianceRequestInTake,
  Event_Ty          VARCHAR(60) NOT NULL REFERENCES ComplianceEventType,
  Entry_Ty          VARCHAR(6)  NOT NULL CHECK (Entry_Ty IN ('AUTO','MANUAL')),
  Received_File_Ref VARCHAR(1100),
  Actor             VARCHAR(100) NOT NULL,
  Detail_Txt        TEXT,
  Event_Dtts        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_filedetail_req ON ComplianceRequestFileDetail (Req_ID, Event_Dtts);

CREATE TABLE CMS_ComplianceExceptionsAudit (
  Event_ID     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Event_Ty     VARCHAR(60) NOT NULL REFERENCES ComplianceEventType,
  Event_Ctgy   VARCHAR(20) NOT NULL CHECK (Event_Ctgy IN ('AUDIT','EXCEPTION')),
  Sevrty       VARCHAR(10) NOT NULL CHECK (Sevrty IN ('INFO','WARNING','ERROR')),
  Project_Cd VARCHAR(30), Table_Nm VARCHAR(63), Src_Cd VARCHAR(30), Run_Ty VARCHAR(20),
  Req_ID       BIGINT REFERENCES ComplianceRequestControl,
  Load_ID      BIGINT REFERENCES ComplianceFileLoad,
  Ovrd_ID      BIGINT REFERENCES ComplianceBatchOverride,
  Extract_ID   BIGINT REFERENCES ComplianceExtractControl,
  Intake_ID    VARCHAR(50) REFERENCES ComplianceRequestInTake,
  Btch_ID      VARCHAR(250),
  File_Ref     VARCHAR(1100),
  Actor        VARCHAR(100) NOT NULL,
  Event_Dtts   TIMESTAMPTZ NOT NULL DEFAULT now(),
  Description  TEXT,
  Notified_Ind SMALLINT NOT NULL DEFAULT 0 CHECK (Notified_Ind IN (0,1))
);
CREATE INDEX ix_excaudit_btch   ON CMS_ComplianceExceptionsAudit (Btch_ID);
CREATE INDEX ix_excaudit_notify ON CMS_ComplianceExceptionsAudit (Event_ID) WHERE Notified_Ind = 0;

-- ================= target-table framework columns (per staging / core table) =================
-- ALTER TABLE <stg>  ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Src_File_Nm VARCHAR(1024) NOT NULL, ADD COLUMN Stg_Load_Dtts TIMESTAMPTZ NOT NULL;
-- CREATE INDEX ON <stg> (Btch_ID, Load_ID);
-- ALTER TABLE <core> ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Current_Ind SMALLINT NOT NULL, ADD COLUMN Load_Dtts TIMESTAMPTZ NOT NULL,
--                    ADD COLUMN End_Dtts TIMESTAMPTZ;
-- CREATE INDEX ON <core> (Btch_ID) WHERE Current_Ind = 1;  CREATE INDEX ON <core> (Load_ID);
