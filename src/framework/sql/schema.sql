-- =============================================================================
-- CMS Compliance Framework - schema (single source of truth; design doc §5 describes it)
-- Target: PostgreSQL 14+ (tested on 16). Requires the btree_gist extension.
-- Applied by `framework init-db`, which creates the metadata schema (FRAMEWORK_METADATA_SCHEMA),
-- sets search_path to it and installs btree_gist first. Object names are intentionally unqualified.
--
-- Not stored here (v4 simplification):
--   * database connections  -> .env locally, AWS Secrets Manager in AWS
--   * runtime settings      -> environment / .env / job arguments
--   * report-period logic   -> framework/period_sql.py (name passed when the job is scheduled)
--   * extract job + params  -> job arguments of the project's evaluate/trigger job
--   * Req_Stat values       -> fixed CHECK list below (transitions enforced in code)
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
  SLA_Days        INT          NOT NULL CHECK (SLA_Days >= 1),                  -- D-38 hold
  Carry_Fwd_Ind   SMALLINT     NOT NULL DEFAULT 0 CHECK (Carry_Fwd_Ind IN (0,1)),  -- D-70: an approved CARRY_FORWARD
                                                                                --  may reuse the prior batch's data
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
-- PostgreSQL database as the metadata (schema-qualified here).
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
CREATE TABLE ComplianceRequestInTake (
  Intake_ID        VARCHAR(50)  PRIMARY KEY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL REFERENCES ComplianceRunType,
  Src_Cd           VARCHAR(30)  REFERENCES ComplianceSourceSystem,
  Req_Ty           VARCHAR(30)  NOT NULL CHECK (Req_Ty IN ('CYCLE_INIT','ADHOC_REQUEST','CORRECTION_REQUEST')),
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Rsn              TEXT,
  Requested_By     VARCHAR(100) NOT NULL,
  Requested_Dtts   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Intake_Stat      VARCHAR(20)  NOT NULL DEFAULT 'NEW'
                   CHECK (Intake_Stat IN ('NEW','PROCESSED','PARTIALLY_PROCESSED','FAILED')),
  Processed_Dtts   TIMESTAMPTZ,
  Error_Txt        TEXT,
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Req_Ty <> 'CORRECTION_REQUEST' OR Src_Cd IS NOT NULL)
);

CREATE TABLE ComplianceExtractControl (
  Extract_ID                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd                VARCHAR(30) NOT NULL,
  Table_Nm                  VARCHAR(63) NOT NULL,
  Run_Ty                    VARCHAR(20) NOT NULL REFERENCES ComplianceRunType,
  Rpt_Start_Dt_Key          DATE NOT NULL,
  Rpt_End_Dt_Key            DATE NOT NULL,
  Required_Src_Cnt          INT  NOT NULL CHECK (Required_Src_Cnt >= 0),
  Received_Src_Cnt          INT  NOT NULL DEFAULT 0,
  Waived_Src_Cnt            INT  NOT NULL DEFAULT 0,
  Included_Src_Cds TEXT, Missing_Src_Cds TEXT, Waived_Src_Cds TEXT,
  Carried_Src_Cds           TEXT,          -- sources counted as received through an approved CARRY_FORWARD
  Extract_Stat              VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (Extract_Stat IN ('PENDING','PARTIAL','COMPLETE')),
  Extract_Rules_Stat        VARCHAR(25) NOT NULL DEFAULT 'PENDING'
                            CHECK (Extract_Rules_Stat IN ('PENDING','PASSED','PASSED_WITH_WARNINGS','FAILED','ERROR')),
  Earliest_Trigger_Dt       DATE NOT NULL,
  Eligibility_Cd            VARCHAR(15) NOT NULL DEFAULT 'NOT_ELIGIBLE'
                            CHECK (Eligibility_Cd IN ('NOT_ELIGIBLE','MANUAL_ONLY','AUTO')),
  Eligibility_Rsn_Txt       VARCHAR(1000),
  Trigger_Stat              VARCHAR(15) NOT NULL DEFAULT 'NOT_TRIGGERED'
                            CHECK (Trigger_Stat IN ('NOT_TRIGGERED','REQUESTED','TRIGGERED','FAILED')),
  Last_Trigger_ID           BIGINT,                                     -- FK added below
  Trigger_Cnt               INT NOT NULL DEFAULT 0,
  Triggered_Combine_Run_Nbr INT,
  Retrigger_Required_Ind    SMALLINT NOT NULL DEFAULT 0 CHECK (Retrigger_Required_Ind IN (0,1)),
  Extract_Close_Ind         SMALLINT NOT NULL DEFAULT 0 CHECK (Extract_Close_Ind IN (0,1)),
  Extract_Closed_Dtts       TIMESTAMPTZ,
  Combine_Run_Cnt           INT NOT NULL DEFAULT 0,
  Combine_Last_Run_Dtts     TIMESTAMPTZ,
  Combine_Last_Trigger_Cd   VARCHAR(20) CHECK (Combine_Last_Trigger_Cd IN
                              ('EARLY_COMPLETE','SLA_EVALUATION','MANUAL_TRIGGER','REOPEN_PROMOTED','OVERRIDE_DECISION','MANUAL_REFRESH')),
  Combine_Btch_ID_List      TEXT,
  Failed_Rule_Refs          TEXT,          -- GATE-failed period rules of the last combine
  Data_Signature            CHAR(64),      -- hash of (Btch_ID, Current_Load_ID) at the last combine
  Triggered_Data_Signature  CHAR(64),      -- Data_Signature at the last successful trigger (drives Retrigger_Required_Ind)
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key),
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Extract_Close_Ind = 0 OR (Trigger_Cnt >= 1 AND Extract_Closed_Dtts IS NOT NULL))
);

CREATE TABLE ComplianceRequestControl (
  Req_ID               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Project_Cd           VARCHAR(30)  NOT NULL,
  Table_Nm             VARCHAR(63)  NOT NULL,
  Src_Cd               VARCHAR(30)  NOT NULL REFERENCES ComplianceSourceSystem,
  Run_Ty               VARCHAR(20)  NOT NULL REFERENCES ComplianceRunType,
  Rpt_Start_Dt_Key     DATE         NOT NULL,
  Rpt_End_Dt_Key       DATE         NOT NULL,
  Req_Dt_Key           DATE         NOT NULL,                           -- actual creation date (D-29)
  Earliest_Close_Dt    DATE         NOT NULL,                           -- D-38
  Btch_ID              VARCHAR(250) NOT NULL UNIQUE,
  Cmplnc_Vrsn          VARCHAR(10)  NOT NULL,
  Extract_ID           BIGINT       NOT NULL REFERENCES ComplianceExtractControl,
  Intake_ID            VARCHAR(50)  REFERENCES ComplianceRequestInTake,  -- origin only, not grain
  Req_Stat             VARCHAR(30)  NOT NULL CHECK (Req_Stat IN ('PENDING','PROMOTED','CARRIED_FORWARD',
                         'EXCEPTION_PENDING','COMPLETED','COMPLETED_WITH_EXCEPTION','DATA_NOT_PROVIDED')),
  Resolution_Ty        VARCHAR(15)  CHECK (Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD','MISSING')),
  Current_Load_ID      BIGINT,                                          -- FK added below
  Reuse_Btch_ID        VARCHAR(250),                                    -- CARRY_FORWARD: batch whose data is reused
  Batch_Close_Ind      SMALLINT     NOT NULL DEFAULT 0 CHECK (Batch_Close_Ind IN (0,1)),
  Closed_By_Trigger_ID BIGINT,                                          -- FK added below
  Created_By           VARCHAR(15)  NOT NULL CHECK (Created_By IN ('SCHEDULER','CYCLE_INIT','ADHOC_INTAKE')),
  Created_Dtts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_Dtts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key),        -- D-30
  CHECK (Rpt_End_Dt_Key >= Rpt_Start_Dt_Key),
  CHECK (Earliest_Close_Dt >= Req_Dt_Key),
  CHECK (Resolution_Ty IS DISTINCT FROM 'NEW_FILE' OR Current_Load_ID IS NOT NULL),
  CHECK (Resolution_Ty IS DISTINCT FROM 'MISSING'  OR Current_Load_ID IS NULL),
  CHECK ((Resolution_Ty = 'CARRY_FORWARD') = (Reuse_Btch_ID IS NOT NULL)),
  CHECK (Resolution_Ty IS DISTINCT FROM 'CARRY_FORWARD' OR Current_Load_ID IS NULL),
  CHECK (Batch_Close_Ind = 0 OR (Resolution_Ty IS NOT NULL AND Closed_By_Trigger_ID IS NOT NULL)),
  CHECK (Created_By NOT IN ('CYCLE_INIT','ADHOC_INTAKE') OR Intake_ID IS NOT NULL)
);
CREATE INDEX ix_crc_open    ON ComplianceRequestControl (Extract_ID) WHERE Batch_Close_Ind = 0;
CREATE INDEX ix_crc_extract ON ComplianceRequestControl (Extract_ID);

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
                            'RULES_RUNNING','RULES_FAILED','PENDING_APPROVAL','PROMOTED','SUPERSEDED','FAILED_TECHNICAL')),
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
CREATE INDEX ix_fileload_btch ON ComplianceFileLoad (Btch_ID, Load_Stat);
CREATE INDEX ix_fileload_sha  ON ComplianceFileLoad (File_Sha256);

CREATE TABLE ComplianceExtractTrigger (
  Trigger_ID           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Extract_ID           BIGINT NOT NULL REFERENCES ComplianceExtractControl,
  Trigger_Ty           VARCHAR(10) NOT NULL CHECK (Trigger_Ty IN ('AUTO','MANUAL','RETRIGGER')),
  Requested_By         VARCHAR(100) NOT NULL,
  Warning_Txt          TEXT,
  Ack_Warnings_Ind     SMALLINT NOT NULL DEFAULT 0 CHECK (Ack_Warnings_Ind IN (0,1)),
  Combine_Run_Nbr      INT NOT NULL,
  Rendered_Params_Txt  TEXT NOT NULL,
  Call_Stat            VARCHAR(10) NOT NULL DEFAULT 'REQUESTED' CHECK (Call_Stat IN ('REQUESTED','SUCCEEDED','FAILED')),
  Job_Run_Ref          VARCHAR(300),
  Response_Txt         TEXT,
  Extract_Job_Ref      VARCHAR(1000),                                  -- Glue job name / endpoint called
  Requested_Dtts       TIMESTAMPTZ NOT NULL DEFAULT now(),
  Completed_Dtts       TIMESTAMPTZ,
  CHECK (Warning_Txt IS NULL OR Ack_Warnings_Ind = 1 OR Trigger_Ty <> 'MANUAL'),
  CHECK (Call_Stat = 'REQUESTED' OR Completed_Dtts IS NOT NULL)
);
CREATE UNIQUE INDEX ux_trigger_one_inflight ON ComplianceExtractTrigger (Extract_ID) WHERE Call_Stat = 'REQUESTED';

ALTER TABLE ComplianceExtractControl ADD CONSTRAINT fk_extract_last_trigger FOREIGN KEY (Last_Trigger_ID) REFERENCES ComplianceExtractTrigger;
ALTER TABLE ComplianceRequestControl ADD CONSTRAINT fk_crc_current_load  FOREIGN KEY (Current_Load_ID) REFERENCES ComplianceFileLoad;
ALTER TABLE ComplianceRequestControl ADD CONSTRAINT fk_crc_close_trigger FOREIGN KEY (Closed_By_Trigger_ID) REFERENCES ComplianceExtractTrigger;

CREATE TABLE ComplianceBatchOverride (
  Ovrd_ID              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Override_Ty          VARCHAR(25) NOT NULL CHECK (Override_Ty IN
                         ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN','SOURCE_WAIVER','RULE_WAIVER','CARRY_FORWARD')),
  Req_ID               BIGINT REFERENCES ComplianceRequestControl,
  Extract_ID           BIGINT NOT NULL REFERENCES ComplianceExtractControl,
  Project_Cd           VARCHAR(30) NOT NULL,
  Table_Nm             VARCHAR(63) NOT NULL,
  Src_Cd               VARCHAR(30),
  Run_Ty               VARCHAR(20) NOT NULL,
  Rpt_Start_Dt_Key     DATE NOT NULL,
  Rpt_End_Dt_Key       DATE NOT NULL,
  Btch_ID              VARCHAR(250),
  Reuse_Btch_ID        VARCHAR(250),                                    -- CARRY_FORWARD: optional; framework picks the latest
  Candidate_Load_ID    BIGINT REFERENCES ComplianceFileLoad,
  Reviewed_Load_ID     BIGINT REFERENCES ComplianceFileLoad,
  Prior_Load_ID        BIGINT REFERENCES ComplianceFileLoad,
  Prior_Resolution_Ty  VARCHAR(15),
  Prior_Req_Stat       VARCHAR(30),
  Rule_Ref             VARCHAR(200),
  Apprvl_Stat          VARCHAR(20) NOT NULL DEFAULT 'PENDING_REVIEW'
                       CHECK (Apprvl_Stat IN ('PENDING_REVIEW','APPROVED','REJECTED','REVOKED')),
  Apprvd_By  VARCHAR(100), Apprvd_Dtts  TIMESTAMPTZ,
  Rejected_By VARCHAR(100), Rejected_Dtts TIMESTAMPTZ, Rejection_Rsn  TEXT,
  Revoked_By  VARCHAR(100), Revoked_Dtts  TIMESTAMPTZ, Revocation_Rsn TEXT,
  Promotion_Stat       VARCHAR(15) NOT NULL DEFAULT 'NOT_APPLICABLE'
                       CHECK (Promotion_Stat IN ('NOT_APPLICABLE','PENDING','PROMOTED','FAILED')),
  Promoted_Dtts        TIMESTAMPTZ,
  Last_Processed_Apprvl_Stat VARCHAR(20),
  History              TEXT NOT NULL,
  Created_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(), Updated_Dtts TIMESTAMPTZ NOT NULL DEFAULT now(),
  Updated_By   VARCHAR(100) NOT NULL DEFAULT current_user,
  CHECK (Override_Ty = 'RULE_WAIVER' OR (Req_ID IS NOT NULL AND Src_Cd IS NOT NULL)),
  CHECK (Override_Ty <> 'RULE_WAIVER' OR Rule_Ref IS NOT NULL),
  CHECK (Override_Ty NOT IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN') OR (Btch_ID IS NOT NULL AND Candidate_Load_ID IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'APPROVED' OR (Apprvd_By IS NOT NULL AND Apprvd_Dtts IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'APPROVED' OR Override_Ty NOT IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN') OR Reviewed_Load_ID = Candidate_Load_ID),
  CHECK (Apprvl_Stat <> 'REVOKED'  OR (Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER','CARRY_FORWARD')
                                     AND Revoked_By IS NOT NULL AND Revoked_Dtts IS NOT NULL AND Revocation_Rsn IS NOT NULL)),
  CHECK (Apprvl_Stat <> 'REJECTED' OR (Rejected_By IS NOT NULL AND Rejected_Dtts IS NOT NULL AND Rejection_Rsn IS NOT NULL))
);
CREATE UNIQUE INDEX ux_ovrd_reopen_active ON ComplianceBatchOverride (Req_ID)
  WHERE Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN') AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE UNIQUE INDEX ux_ovrd_batch_decision_active ON ComplianceBatchOverride (Req_ID)   -- a batch is waived OR carried
  WHERE Override_Ty IN ('SOURCE_WAIVER','CARRY_FORWARD') AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE UNIQUE INDEX ux_ovrd_rulewaiver_active ON ComplianceBatchOverride (Extract_ID, Rule_Ref)
  WHERE Override_Ty = 'RULE_WAIVER' AND Apprvl_Stat IN ('PENDING_REVIEW','APPROVED');
CREATE INDEX ix_ovrd_decisions ON ComplianceBatchOverride (Ovrd_ID)
  WHERE Apprvl_Stat IS DISTINCT FROM Last_Processed_Apprvl_Stat;

-- ================= audit =================
CREATE TABLE ComplianceRequestFileDetail (
  Detail_ID         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  Req_ID            BIGINT NOT NULL REFERENCES ComplianceRequestControl,
  Btch_ID           VARCHAR(250) NOT NULL,
  Load_ID           BIGINT REFERENCES ComplianceFileLoad,
  Ovrd_ID           BIGINT REFERENCES ComplianceBatchOverride,
  Intake_ID         VARCHAR(50) REFERENCES ComplianceRequestInTake,
  Trigger_ID        BIGINT REFERENCES ComplianceExtractTrigger,
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
  Trigger_ID   BIGINT REFERENCES ComplianceExtractTrigger,
  Btch_ID      VARCHAR(250),
  File_Ref     VARCHAR(1100),
  Actor        VARCHAR(100) NOT NULL,
  Event_Dtts   TIMESTAMPTZ NOT NULL DEFAULT now(),
  Description  TEXT,
  Notified_Ind SMALLINT NOT NULL DEFAULT 0 CHECK (Notified_Ind IN (0,1))
);
CREATE INDEX ix_excaudit_btch   ON CMS_ComplianceExceptionsAudit (Btch_ID);
CREATE INDEX ix_excaudit_notify ON CMS_ComplianceExceptionsAudit (Event_ID) WHERE Notified_Ind = 0;

CREATE VIEW vw_crc_current_flags AS
SELECT c.Req_ID,
       EXISTS (SELECT 1 FROM ComplianceRequestFileDetail d
                WHERE d.Req_ID = c.Req_ID AND d.Event_Ty = 'LATE_ARRIVAL_RECEIVED') AS Was_Late_Arrival,
       COALESCE((SELECT d.Event_Ty FROM ComplianceRequestFileDetail d
                  WHERE d.Req_ID = c.Req_ID AND d.Event_Ty IN ('CORRECTION_FLAGGED','CORRECTION_FLAG_CLEARED')
                  ORDER BY d.Event_Dtts DESC, d.Detail_ID DESC LIMIT 1) = 'CORRECTION_FLAGGED', FALSE)
         AS Currently_Flagged_For_Correction
  FROM ComplianceRequestControl c;

-- ================= target-table framework columns (per staging / core table) =================
-- ALTER TABLE <stg>  ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Src_File_Nm VARCHAR(1024) NOT NULL, ADD COLUMN Stg_Load_Dtts TIMESTAMPTZ NOT NULL;
-- CREATE INDEX ON <stg> (Btch_ID, Load_ID);
-- ALTER TABLE <core> ADD COLUMN Btch_ID VARCHAR(250) NOT NULL, ADD COLUMN Load_ID BIGINT NOT NULL,
--                    ADD COLUMN Current_Ind SMALLINT NOT NULL, ADD COLUMN Load_Dtts TIMESTAMPTZ NOT NULL,
--                    ADD COLUMN End_Dtts TIMESTAMPTZ;
-- CREATE INDEX ON <core> (Btch_ID) WHERE Current_Ind = 1;  CREATE INDEX ON <core> (Load_ID);
