-- CMS Compliance Framework schema for Teradata. Replace {{META_DB}} with the metadata database and run once.
-- No secondary indexes: each table has only its primary index. Existing installations: upgrade_0.3.71.sql.

CREATE MULTISET TABLE {{META_DB}}.ComplianceProject (
  Project_Cd    VARCHAR(30)  NOT NULL,
  Project_Desc  VARCHAR(200) NOT NULL,
  Active_Ind    SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID    VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID    VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd);

-- Optional: the table group of a project's tables, used only when a run names a table group.
CREATE MULTISET TABLE {{META_DB}}.ComplianceTable (
  Project_Cd    VARCHAR(30)  NOT NULL,
  Table_Nm      VARCHAR(63)  NOT NULL,
  Table_Grp_Cd  VARCHAR(30)  NOT NULL,
  Table_Desc    VARCHAR(200),
  Active_Ind    SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID    VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID    VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm);

-- Optional: the export table of a core table (EXPORT_LOAD), one row per project / table.
CREATE MULTISET TABLE {{META_DB}}.ComplianceExportConfig (
  Project_Cd      VARCHAR(30)    NOT NULL,
  Table_Nm        VARCHAR(63)    NOT NULL,
  Exp_Schema_Nm   VARCHAR(128)   NOT NULL,
  Exp_Table_Nm    VARCHAR(63)    NOT NULL,
  Exp_Btch_Col_Nm VARCHAR(128),
  Exp_Proc_Nm     VARCHAR(256),
  Exp_Proc_Parm_Txt VARCHAR(1000),
  Exp_Sql_Txt       VARCHAR(8000),
  Exp_Pre_Sql_Txt   VARCHAR(8000),
  Exp_Post_Sql_Txt  VARCHAR(8000),
  Active_Ind      SMALLINT       DEFAULT 1 NOT NULL,
  Load_Dtts    TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID      VARCHAR(100)   DEFAULT USER NOT NULL,
  LastUpdt_Dtts    TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID      VARCHAR(100)   DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm);

-- Optional: internal sources pulled with a query (SOURCE_LOAD), one row per project / table / source.
CREATE MULTISET TABLE {{META_DB}}.ComplianceInternalSourceConfig (
  Project_Cd      VARCHAR(30)    NOT NULL,
  Table_Nm        VARCHAR(63)    NOT NULL,
  Src_ID          VARCHAR(30)    NOT NULL,
  Src_Db_Ty       VARCHAR(20)    NOT NULL,
  Src_Conn_Cd     VARCHAR(100),
  Src_Sql_Txt     VARCHAR(32000) NOT NULL,
  Stg_Schema_Nm   VARCHAR(128)   NOT NULL,
  Stg_Table_Nm    VARCHAR(63)    NOT NULL,
  Core_Schema_Nm  VARCHAR(128)   NOT NULL,
  Core_Table_Nm   VARCHAR(63)    NOT NULL,
  Active_Ind      SMALLINT       DEFAULT 1 NOT NULL,
  Load_Dtts    TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID      VARCHAR(100)   DEFAULT USER NOT NULL,
  LastUpdt_Dtts    TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID      VARCHAR(100)   DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Src_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceSourceSystem (
  Src_ID        VARCHAR(30)  NOT NULL,
  Src_Nm        VARCHAR(100) NOT NULL,
  Src_Ty        VARCHAR(20)  NOT NULL,
  Active_Ind    SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID    VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID    VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Src_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRunType (
  Run_Ty          VARCHAR(20)  NOT NULL,
  Run_Ty_Desc     VARCHAR(200) NOT NULL,
  Run_Category_Cd VARCHAR(10)  NOT NULL,
  SLA_Days        INTEGER      NOT NULL,
  Carry_Fwd_Ind   SMALLINT     DEFAULT 0 NOT NULL,
  Sched_Cd        VARCHAR(10),
  Sched_Day_Cd    VARCHAR(5),
  Sched_Week_Cd   VARCHAR(5),
  Sched_Months_Txt VARCHAR(40),
  Sched_Window_Days SMALLINT    DEFAULT 1 NOT NULL,
  Sched_Offset_Days SMALLINT    DEFAULT 0 NOT NULL,
  Batch_Schedule_Sql_Txt VARCHAR(8000),
  Active_Ind      SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts    TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID      VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts    TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID      VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Run_Ty);

-- Optional: batches created on given dates with given report dates (irregular schedules), per run type and table.
CREATE MULTISET TABLE {{META_DB}}.ComplianceBatchCalendar (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  DEFAULT '*' NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL,
  Req_Dt_Key       DATE         NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Active_Ind       SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Run_Ty, Req_Dt_Key, Rpt_Start_Dt_Key, Rpt_End_Dt_Key);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRunTypePeriod (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  DEFAULT '*' NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL,
  Rpt_Period_Cd    VARCHAR(60)  ,
  Rpt_Dt_Sql_Txt   VARCHAR(8000),
  Active_Ind       SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Run_Ty);

CREATE MULTISET TABLE {{META_DB}}.ComplianceDataSetSourceXwalk (
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Run_Ty                 VARCHAR(200)  NOT NULL,
  Effective_Start_Dt_Key DATE         NOT NULL,
  Effective_End_Dt_Key   DATE,
  Cmplnc_Vrsn            VARCHAR(10)  NOT NULL,
  Rpt_Period_Cd          VARCHAR(60),
  Rpt_Dt_Sql_Txt         VARCHAR(8000),
  Include_Ind            SMALLINT     DEFAULT 1 NOT NULL,
  Active_Ind             SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts           TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID             VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts           TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID             VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key);

CREATE MULTISET TABLE {{META_DB}}.ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT GENERATED ALWAYS AS IDENTITY,
  Project_Cd             VARCHAR(30)  NOT NULL,
  Table_Nm               VARCHAR(63)  NOT NULL,
  Src_ID                 VARCHAR(30)  NOT NULL,
  Src_File_Nm_Tmplt      VARCHAR(255) NOT NULL,
  File_Vrsn_Cd           VARCHAR(30),
  Delmtr_Cd              VARCHAR(10),
  Src_File_Has_Hdr_Ind   SMALLINT     NOT NULL,
  Src_File_Has_Trlr_Ind  SMALLINT     NOT NULL,
  Allow_Zero_Rcd_Ind     SMALLINT     NOT NULL,
  Src_File_Path          VARCHAR(500) NOT NULL,
  Stg_Schema_Nm          VARCHAR(63)  NOT NULL,
  Stg_Table_Nm           VARCHAR(63)  NOT NULL,
  Core_Schema_Nm         VARCHAR(63)  NOT NULL,
  Core_Table_Nm          VARCHAR(63)  NOT NULL,
  Active_Ind             SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts           TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID             VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts           TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID             VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Cfg_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceFileCheckConfig (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  DEFAULT '*' NOT NULL,
  Src_ID           VARCHAR(30)  DEFAULT '*' NOT NULL,
  Check_Cd         VARCHAR(30)  NOT NULL,
  Check_Field_Nm   VARCHAR(128) DEFAULT '*' NOT NULL,
  Check_Val_Txt    VARCHAR(8000),
  Include_Ind      SMALLINT     DEFAULT 1 NOT NULL,
  Active_Ind       SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Src_ID, Check_Cd);

CREATE MULTISET TABLE {{META_DB}}.ComplianceTargetColumnConfig (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  DEFAULT '*' NOT NULL,
  Column_Nm        VARCHAR(128) NOT NULL,
  Value_Cd         VARCHAR(30)  NOT NULL,
  Value_Txt        VARCHAR(8000),
  Active_Ind       SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Column_Nm);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRuleBinding (
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  DEFAULT '*' NOT NULL,
  Gre_Rule_Group   VARCHAR(100) NOT NULL,
  Gre_Rule_Variant VARCHAR(100) NOT NULL,
  Active_Ind       SMALLINT     DEFAULT 1 NOT NULL,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Project_Cd, Table_Nm, Src_ID, Run_Ty, Gre_Rule_Group, Gre_Rule_Variant);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRequestInTake (
  Intake_ID        BIGINT GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL,
  Cmplnc_Vrsn      VARCHAR(10)  NOT NULL,
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Start_Dt_Key DATE         NOT NULL,
  Req_End_Dt_Key   DATE         NOT NULL,
  Last_Run_Dt_Key  DATE,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Intake_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceRequestControl (
  Req_ID           BIGINT GENERATED ALWAYS AS IDENTITY,
  Project_Cd       VARCHAR(30)  NOT NULL,
  Table_Nm         VARCHAR(63)  NOT NULL,
  Src_ID           VARCHAR(30)  NOT NULL,
  Run_Ty           VARCHAR(20)  NOT NULL,
  Cmplnc_Vrsn      VARCHAR(10),
  Rpt_Start_Dt_Key DATE         NOT NULL,
  Rpt_End_Dt_Key   DATE         NOT NULL,
  Req_Dt_Key       DATE         NOT NULL,
  Btch_ID          VARCHAR(250) NOT NULL,
  Req_Stat         VARCHAR(30)  NOT NULL,
  Resolution_Ty    VARCHAR(15),
  Reuse_Btch_ID    VARCHAR(250),
  Batch_Close_Ind  SMALLINT     DEFAULT 0 NOT NULL,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID      VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID      VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Req_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceFileLoad (
  Load_ID           BIGINT GENERATED ALWAYS AS IDENTITY,
  Cfg_ID            BIGINT,
  Req_ID            BIGINT,
  Btch_ID           VARCHAR(250),
  File_Hash         CHAR(64)      NOT NULL,
  File_Share        VARCHAR(200)  NOT NULL,
  File_Path         VARCHAR(1024) NOT NULL,
  File_Version      VARCHAR(200)  NOT NULL,
  File_Size_Byte    BIGINT        NOT NULL,
  File_Sha256       CHAR(64),
  Load_Stat         VARCHAR(25)   NOT NULL,
  Rules_Stat        VARCHAR(25)   DEFAULT 'NOT_RUN' NOT NULL,
  Reject_Rsn_Cd     VARCHAR(60),
  Stg_Rcd_Cnt       BIGINT,
  Error_Txt         VARCHAR(4000),
  Load_Dtts      TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID       VARCHAR(100)  DEFAULT USER NOT NULL,
  LastUpdt_Dtts      TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID       VARCHAR(100)  DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Load_ID);

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
  Load_Dtts      TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LoadUser_ID        VARCHAR(100) DEFAULT USER NOT NULL,
  LastUpdt_Dtts      TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL,
  LastUpdt_ID        VARCHAR(100) DEFAULT USER NOT NULL
)
UNIQUE PRIMARY INDEX (Ovrd_ID);

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
  Event_Dtts  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL
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
  Notified_Ind SMALLINT      DEFAULT 0 NOT NULL,
  Event_Dtts   TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) NOT NULL
)
UNIQUE PRIMARY INDEX (Event_ID);

CREATE MULTISET TABLE {{META_DB}}.ComplianceLock (
  Lock_Key      VARCHAR(300) NOT NULL,
  Owner_ID      VARCHAR(64)  NOT NULL,
  Acquired_Dtts TIMESTAMP(6) WITH TIME ZONE NOT NULL,
  Expires_Dtts  TIMESTAMP(6) WITH TIME ZONE NOT NULL
)
UNIQUE PRIMARY INDEX (Lock_Key);
