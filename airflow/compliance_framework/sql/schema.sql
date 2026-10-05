-- CMS Compliance Framework schema for Teradata (v6). Replace {{META_DB}} with the metadata database and run once.

CREATE MULTISET TABLE {{META_DB}}.ComplianceProject (
  Project_Cd    VARCHAR(30)  NOT NULL,
  Project_Desc  VARCHAR(200) NOT NULL,
  Active_Ind    SMALLINT     DEFAULT 1 NOT NULL,
  Created_Dtts  TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By    VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts  TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By    VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd);

CREATE MULTISET TABLE {{META_DB}}.ComplianceSourceSystem (
  Src_ID        VARCHAR(30)  NOT NULL,
  Src_Nm        VARCHAR(100) NOT NULL,
  Src_Ty        VARCHAR(20)  NOT NULL,
  Active_Ind    SMALLINT     DEFAULT 1 NOT NULL,
  Created_Dtts  TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By    VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts  TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By    VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Src_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRunType (
  Run_Ty          VARCHAR(20)  NOT NULL,
  Run_Ty_Desc     VARCHAR(200) NOT NULL,
  Run_Category_Cd VARCHAR(10)  NOT NULL,
  SLA_Days        INTEGER      NOT NULL,
  Carry_Fwd_Ind   SMALLINT     DEFAULT 0 NOT NULL,
  Batch_Sql_Txt   VARCHAR(16000),
  Active_Ind      SMALLINT     DEFAULT 1 NOT NULL,
  Created_Dtts    TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By      VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts    TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By      VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Run_Ty);

CREATE MULTISET TABLE {{META_DB}}.ComplianceDataSetSourceXwalk (
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Run_Ty                 VARCHAR(20)  NOT NULL,
  Effective_Start_Dt_Key DATE         NOT NULL,
  Effective_End_Dt_Key   DATE,
  Cmplnc_Vrsn            VARCHAR(10)  NOT NULL,
  Active_Ind             SMALLINT     DEFAULT 1 NOT NULL,
  Created_Dtts           TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By             VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts           TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By             VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key);

CREATE MULTISET TABLE {{META_DB}}.ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT GENERATED ALWAYS AS IDENTITY,
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Src_File_Nm_Tmplt      VARCHAR(255) NOT NULL,
  Delmtr_Cd              VARCHAR(10),
  Src_File_Has_Hdr_Ind   SMALLINT     NOT NULL,
  Src_File_Has_Trlr_Ind  SMALLINT     NOT NULL,
  Allow_Zero_Rcd_Ind     SMALLINT     NOT NULL,
  Src_File_Path          VARCHAR(500) NOT NULL,
  Stg_Schema_Nm          VARCHAR(63)  NOT NULL,
  Stg_Table_Nm           VARCHAR(63)  NOT NULL,
  Core_Schema_Nm         VARCHAR(63)  NOT NULL,
  Sucs_Email_Notfn_Id    VARCHAR(2000),
  Failr_Email_Notfn_Id   VARCHAR(2000),
  Email_Subjct_Txt       VARCHAR(200),
  Active_Ind             SMALLINT     DEFAULT 1 NOT NULL,
  Created_Dtts           TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By             VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts           TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By             VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Cfg_ID);

CREATE INDEX ix_fileconfig_source (Project_Cd, Table_Nm, Src_ID)
ON {{META_DB}}.ComplianceSourceFileConfig;

CREATE MULTISET TABLE {{META_DB}}.ComplianceRuleBinding (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  DEFAULT '*' NOT NULL,
  Gre_Rule_Group   VARCHAR(100) NOT NULL,
  Gre_Rule_Variant VARCHAR(100) NOT NULL,
  Active_Ind       SMALLINT     DEFAULT 1 NOT NULL,
  Created_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By       VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Src_ID, Run_Ty, Gre_Rule_Group, Gre_Rule_Variant);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRequestInTake (
  Intake_ID        BIGINT GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30),
  Run_Ty           VARCHAR(20)  NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Start_Dt_Key DATE         NOT NULL,
  Req_End_Dt_Key   DATE         NOT NULL,
  Last_Run_Dt_Key  DATE,
  Created_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By       VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Intake_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRequestControl (
  Req_ID           BIGINT GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Dt_Key       DATE         NOT NULL,
  Btch_ID          VARCHAR(250) NOT NULL,
  Req_Stat         VARCHAR(30)  NOT NULL,
  Resolution_Ty    VARCHAR(15),
  Reuse_Btch_ID    VARCHAR(250),
  Batch_Close_Ind  SMALLINT     DEFAULT 0 NOT NULL,
  Created_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL
)
UNIQUE PRIMARY INDEX (Req_ID);

CREATE UNIQUE INDEX uq_reqcontrol_btch (Btch_ID)
ON {{META_DB}}.ComplianceRequestControl;

CREATE UNIQUE INDEX uq_reqcontrol_key (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key)
ON {{META_DB}}.ComplianceRequestControl;

CREATE MULTISET TABLE {{META_DB}}.ComplianceFileLoad (
  Load_ID           BIGINT GENERATED ALWAYS AS IDENTITY,
  File_Hash         CHAR(64)      NOT NULL,
  File_Share        VARCHAR(200)  NOT NULL,
  File_Path         VARCHAR(1024) NOT NULL,
  File_Version      VARCHAR(200)  NOT NULL,
  File_Size_Byte    BIGINT        NOT NULL,
  File_Sha256       CHAR(64),
  Cfg_ID            BIGINT,
  Req_ID            BIGINT,
  Btch_ID           VARCHAR(250),
  Load_Stat         VARCHAR(25)   NOT NULL,
  Rules_Stat        VARCHAR(25)   DEFAULT 'NOT_RUN' NOT NULL,
  Quarantine_Rsn_Cd VARCHAR(60),
  Stg_Rcd_Cnt       BIGINT,
  Error_Txt         VARCHAR(4000),
  Created_Dtts      TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_Dtts      TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL
)
UNIQUE PRIMARY INDEX (Load_ID);

CREATE UNIQUE INDEX uq_fileload_file (File_Hash)
ON {{META_DB}}.ComplianceFileLoad;

CREATE INDEX ix_fileload_batch (Btch_ID)
ON {{META_DB}}.ComplianceFileLoad;

CREATE INDEX ix_fileload_sha (File_Sha256)
ON {{META_DB}}.ComplianceFileLoad;

CREATE INDEX ix_fileload_path (File_Path)
ON {{META_DB}}.ComplianceFileLoad;

CREATE MULTISET TABLE {{META_DB}}.ComplianceBatchOverride (
  Ovrd_ID           BIGINT GENERATED ALWAYS AS IDENTITY,
  Override_Ty       VARCHAR(20)  NOT NULL,
  Req_ID            BIGINT       NOT NULL,
  Btch_ID           VARCHAR(250) NOT NULL,
  Reuse_Btch_ID     VARCHAR(250),
  Apprvl_Stat       VARCHAR(20)  DEFAULT 'PENDING_REVIEW' NOT NULL,
  Valid_Thru_Dt_Key DATE,
  Rsn_Txt           VARCHAR(4000),
  Reviewed_By       VARCHAR(100),
  Reviewed_Dtts     TIMESTAMP(6) WITH TIME ZONE,
  Created_Dtts      TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Created_By        VARCHAR(100) DEFAULT USER NOT NULL,
  Updated_Dtts      TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Updated_By        VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Ovrd_ID);

CREATE INDEX ix_override_batch (Req_ID)
ON {{META_DB}}.ComplianceBatchOverride;

CREATE MULTISET TABLE {{META_DB}}.ComplianceRequestFileDetail (
  Detail_ID   BIGINT GENERATED ALWAYS AS IDENTITY,
  Req_ID      BIGINT       NOT NULL,
  Btch_ID     VARCHAR(250) NOT NULL,
  Load_ID     BIGINT,
  Ovrd_ID     BIGINT,
  Intake_ID   BIGINT,
  Event_Ty    VARCHAR(60)  NOT NULL,
  Actor       VARCHAR(100) NOT NULL,
  Event_Txt   VARCHAR(4000),
  Event_Dtts  TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL
)
PRIMARY INDEX (Req_ID);

CREATE MULTISET TABLE {{META_DB}}.CMS_ComplianceExceptionsAudit (
  Event_ID     BIGINT GENERATED ALWAYS AS IDENTITY,
  Event_Ty     VARCHAR(60)   NOT NULL,
  Sevrty       VARCHAR(10)   NOT NULL,
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
  Event_Txt    VARCHAR(4000),
  Event_Dtts   TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  Notified_Ind SMALLINT      DEFAULT 0 NOT NULL
)
UNIQUE PRIMARY INDEX (Event_ID);

CREATE INDEX ix_audit_unsent (Notified_Ind)
ON {{META_DB}}.CMS_ComplianceExceptionsAudit;

CREATE INDEX ix_audit_load (Load_ID)
ON {{META_DB}}.CMS_ComplianceExceptionsAudit;

CREATE MULTISET TABLE {{META_DB}}.ComplianceLock (
  Lock_Key      VARCHAR(300) NOT NULL,
  Owner_ID      VARCHAR(64)  NOT NULL,
  Acquired_Dtts TIMESTAMP(6) WITH TIME ZONE NOT NULL,
  Expires_Dtts  TIMESTAMP(6) WITH TIME ZONE NOT NULL
)
UNIQUE PRIMARY INDEX (Lock_Key);
