-- CMS Compliance Framework schema (v6). Run once with search_path set to the metadata schema.

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
CREATE UNIQUE INDEX uq_complianceproject_code ON ComplianceProject (UPPER(Project_Cd));

CREATE TABLE ComplianceSourceSystem (
  Src_ID        VARCHAR(30)  NOT NULL,
  Src_Nm        VARCHAR(100) NOT NULL,
  Src_Ty        VARCHAR(20)  NOT NULL,
  Active_Ind    SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By    VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By    VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancesourcesystem PRIMARY KEY (Src_ID)
);
CREATE UNIQUE INDEX uq_compliancesourcesystem_code ON ComplianceSourceSystem (UPPER(Src_ID));

CREATE TABLE ComplianceRunType (
  Run_Ty          VARCHAR(20)  NOT NULL,
  Run_Ty_Desc     VARCHAR(200) NOT NULL,
  Run_Category_Cd VARCHAR(10)  NOT NULL,
  SLA_Days        INT          NOT NULL,
  Carry_Fwd_Ind   SMALLINT     NOT NULL DEFAULT 0,
  Batch_Schedule_Sql_Txt TEXT,
  Active_Ind      SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By      VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By      VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_complianceruntype PRIMARY KEY (Run_Ty)
);
CREATE UNIQUE INDEX uq_complianceruntype_code ON ComplianceRunType (UPPER(Run_Ty));

CREATE TABLE ComplianceDataSetSourceXwalk (
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Run_Ty                 VARCHAR(20)  NOT NULL,
  Effective_Start_Dt_Key DATE         NOT NULL,
  Effective_End_Dt_Key   DATE,
  Cmplnc_Vrsn            VARCHAR(10)  NOT NULL,
  Rpt_Dt_Sql_Txt         TEXT,
  Active_Ind             SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancedatasetsourcexwalk PRIMARY KEY (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key)
);

CREATE TABLE ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT       GENERATED ALWAYS AS IDENTITY,
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Src_File_Nm_Tmplt      VARCHAR(255) NOT NULL,
  Delmtr_Cd              VARCHAR(10),
  Src_File_Has_Hdr_Ind   SMALLINT     NOT NULL,
  Src_File_Has_Trlr_Ind  SMALLINT     NOT NULL,
  Allow_Zero_Rcd_Ind     SMALLINT     NOT NULL,
  S3_Src_File_Path       VARCHAR(500) NOT NULL,
  Stg_Schema_Nm          VARCHAR(63)  NOT NULL,
  Stg_Table_Nm           VARCHAR(63)  NOT NULL,
  Core_Schema_Nm         VARCHAR(63)  NOT NULL,
  Sucs_Email_Notfn_Id    TEXT,
  Failr_Email_Notfn_Id   TEXT,
  Email_Subjct_Txt       VARCHAR(200),
  Active_Ind             SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By             VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancesourcefileconfig PRIMARY KEY (Cfg_ID)
);
CREATE UNIQUE INDEX uq_compliancesourcefileconfig_active
  ON ComplianceSourceFileConfig (UPPER(Project_Cd), UPPER(Table_Nm), UPPER(Src_ID)) WHERE Active_Ind = 1;

CREATE TABLE ComplianceRuleBinding (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL DEFAULT '*',
  Gre_Rule_Group   VARCHAR(100) NOT NULL,
  Gre_Rule_Variant VARCHAR(100) NOT NULL,
  Active_Ind       SMALLINT     NOT NULL DEFAULT 1,
  Created_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancerulebinding PRIMARY KEY (Project_Cd, Table_Nm, Src_ID, Run_Ty, Gre_Rule_Group, Gre_Rule_Variant)
);

CREATE TABLE ComplianceRequestInTake (
  Intake_ID        BIGINT       GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30),
  Run_Ty           VARCHAR(20)  NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Start_Dt_Key DATE         NOT NULL,
  Req_End_Dt_Key   DATE         NOT NULL,
  Last_Run_Dt_Key  DATE,
  Created_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Created_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  Updated_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_By       VARCHAR(100) NOT NULL DEFAULT current_user,
  CONSTRAINT pk_compliancerequestintake PRIMARY KEY (Intake_ID)
);

CREATE TABLE ComplianceRequestControl (
  Req_ID           BIGINT       GENERATED ALWAYS AS IDENTITY,
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
  Batch_Close_Ind  SMALLINT     NOT NULL DEFAULT 0,
  Created_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  Updated_Dtts     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT pk_compliancerequestcontrol PRIMARY KEY (Req_ID),
  CONSTRAINT uq_compliancerequestcontrol_btch UNIQUE (Btch_ID),
  CONSTRAINT uq_compliancerequestcontrol UNIQUE (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key)
);
CREATE INDEX ix_compliancerequestcontrol_open ON ComplianceRequestControl (Req_ID) WHERE Batch_Close_Ind = 0;

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
  Load_Stat         VARCHAR(25)   NOT NULL,
  Rules_Stat        VARCHAR(25)   NOT NULL DEFAULT 'NOT_RUN',
  Quarantine_Rsn_Cd VARCHAR(60),
  Stg_Rcd_Cnt       BIGINT,
  Error_Txt         TEXT,
  Created_Dtts      TIMESTAMPTZ   NOT NULL DEFAULT now(),
  Updated_Dtts      TIMESTAMPTZ   NOT NULL DEFAULT now(),
  CONSTRAINT pk_compliancefileload PRIMARY KEY (Load_ID),
  CONSTRAINT fk_compliancefileload_config FOREIGN KEY (Cfg_ID) REFERENCES ComplianceSourceFileConfig,
  CONSTRAINT fk_compliancefileload_batch  FOREIGN KEY (Req_ID) REFERENCES ComplianceRequestControl
);
CREATE UNIQUE INDEX uq_compliancefileload_object   ON ComplianceFileLoad (S3_Bucket, S3_Key, COALESCE(S3_Version_Id, S3_ETag));
CREATE UNIQUE INDEX uq_compliancefileload_promoted ON ComplianceFileLoad (Btch_ID) WHERE Load_Stat = 'PROMOTED';
CREATE INDEX ix_compliancefileload_sha ON ComplianceFileLoad (File_Sha256);

CREATE TABLE ComplianceBatchOverride (
  Ovrd_ID           BIGINT       GENERATED ALWAYS AS IDENTITY,
  Override_Ty       VARCHAR(20)  NOT NULL,
  Req_ID            BIGINT       NOT NULL,
  Btch_ID           VARCHAR(250) NOT NULL,
  Reuse_Btch_ID     VARCHAR(250),
  Apprvl_Stat       VARCHAR(20)  NOT NULL DEFAULT 'PENDING_REVIEW',
  Valid_Thru_Dt_Key DATE,
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
  ON ComplianceBatchOverride (Req_ID, UPPER(Override_Ty)) WHERE UPPER(Apprvl_Stat) IN ('PENDING_REVIEW', 'APPROVED');

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

CREATE TABLE CMS_ComplianceExceptionsAudit (
  Event_ID     BIGINT        GENERATED ALWAYS AS IDENTITY,
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
CREATE INDEX ix_cms_complianceexceptionsaudit_once ON CMS_ComplianceExceptionsAudit (Event_Ty, Load_ID, Ovrd_ID)
  WHERE Event_Ty IN ('FILE_TECHNICAL_FAILURE', 'OVERRIDE_INVALID_DETECTED');
