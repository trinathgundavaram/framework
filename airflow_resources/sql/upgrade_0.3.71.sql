-- One-time upgrade to 0.3.71 of the DEV tables (replace HSABC_DEV). Part 1: the audit columns renamed in every
-- framework table. Part 2: six tables recreated in the schema.sql column order. No indexes are created or dropped.
-- Created_Dtts -> Load_Dtts, Created_By -> LoadUser_ID, Updated_Dtts -> LastUpdt_Dtts, Updated_By -> LastUpdt_ID.
-- Run once, with the DAGs paused, before deploying framework 0.3.71. Data, types and defaults are kept.

-- Afterwards airflow_resources/sql/schema_check.sql should return no rows.

-- ---------------------------------------------------------------------------------------------------------------
-- Part 1: renames

-- ComplianceProject
ALTER TABLE HSABC_DEV.ComplianceProject RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceProject RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceProject RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceProject RENAME Updated_By TO LastUpdt_ID;

-- ComplianceTable
ALTER TABLE HSABC_DEV.ComplianceTable RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceTable RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceTable RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceTable RENAME Updated_By TO LastUpdt_ID;

-- ComplianceExportConfig
ALTER TABLE HSABC_DEV.ComplianceExportConfig RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceExportConfig RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceExportConfig RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceExportConfig RENAME Updated_By TO LastUpdt_ID;

-- ComplianceInternalSourceConfig
ALTER TABLE HSABC_DEV.ComplianceInternalSourceConfig RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceInternalSourceConfig RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceInternalSourceConfig RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceInternalSourceConfig RENAME Updated_By TO LastUpdt_ID;

-- ComplianceSourceSystem
ALTER TABLE HSABC_DEV.ComplianceSourceSystem RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceSourceSystem RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceSourceSystem RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceSourceSystem RENAME Updated_By TO LastUpdt_ID;

-- ComplianceRunType
ALTER TABLE HSABC_DEV.ComplianceRunType RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRunType RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceRunType RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRunType RENAME Updated_By TO LastUpdt_ID;

-- ComplianceBatchCalendar
ALTER TABLE HSABC_DEV.ComplianceBatchCalendar RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceBatchCalendar RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceBatchCalendar RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceBatchCalendar RENAME Updated_By TO LastUpdt_ID;

-- ComplianceRunTypePeriod
ALTER TABLE HSABC_DEV.ComplianceRunTypePeriod RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRunTypePeriod RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceRunTypePeriod RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRunTypePeriod RENAME Updated_By TO LastUpdt_ID;

-- ComplianceDataSetSourceXwalk
ALTER TABLE HSABC_DEV.ComplianceDataSetSourceXwalk RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceDataSetSourceXwalk RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceDataSetSourceXwalk RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceDataSetSourceXwalk RENAME Updated_By TO LastUpdt_ID;

-- ComplianceSourceFileConfig
ALTER TABLE HSABC_DEV.ComplianceSourceFileConfig RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceSourceFileConfig RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceSourceFileConfig RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceSourceFileConfig RENAME Updated_By TO LastUpdt_ID;

-- ComplianceFileCheckConfig
ALTER TABLE HSABC_DEV.ComplianceFileCheckConfig RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceFileCheckConfig RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceFileCheckConfig RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceFileCheckConfig RENAME Updated_By TO LastUpdt_ID;

-- ComplianceTargetColumnConfig
ALTER TABLE HSABC_DEV.ComplianceTargetColumnConfig RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceTargetColumnConfig RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceTargetColumnConfig RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceTargetColumnConfig RENAME Updated_By TO LastUpdt_ID;

-- ComplianceRuleBinding
ALTER TABLE HSABC_DEV.ComplianceRuleBinding RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRuleBinding RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceRuleBinding RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRuleBinding RENAME Updated_By TO LastUpdt_ID;

-- ComplianceRequestInTake
ALTER TABLE HSABC_DEV.ComplianceRequestInTake RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRequestInTake RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceRequestInTake RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRequestInTake RENAME Updated_By TO LastUpdt_ID;

-- ComplianceRequestControl
ALTER TABLE HSABC_DEV.ComplianceRequestControl RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceRequestControl RENAME Updated_Dtts TO LastUpdt_Dtts;

-- ComplianceFileLoad
ALTER TABLE HSABC_DEV.ComplianceFileLoad RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceFileLoad RENAME Updated_Dtts TO LastUpdt_Dtts;

-- ComplianceBatchOverride
ALTER TABLE HSABC_DEV.ComplianceBatchOverride RENAME Created_Dtts TO Load_Dtts;
ALTER TABLE HSABC_DEV.ComplianceBatchOverride RENAME Created_By TO LoadUser_ID;
ALTER TABLE HSABC_DEV.ComplianceBatchOverride RENAME Updated_Dtts TO LastUpdt_Dtts;
ALTER TABLE HSABC_DEV.ComplianceBatchOverride RENAME Updated_By TO LastUpdt_ID;

-- ---------------------------------------------------------------------------------------------------------------
-- Part 2: column order. Teradata cannot move a column, so these six tables are backed up, dropped, created in the
-- schema.sql order and filled again from the backup. Run after Part 1 (the backups then carry the new names).
-- ComplianceRequestControl and ComplianceFileLoad get LoadUser_ID / LastUpdt_ID; existing rows take the user
-- running this script.
--
-- IDs: Cfg_ID, Req_ID, Load_ID and Event_ID are referenced by other tables and must keep their values. A
-- GENERATED ALWAYS column cannot take a copied value, so these four are created GENERATED BY DEFAULT, starting
-- after the highest copied ID. The framework never supplies an ID, so new rows are numbered as before.
-- Step 2.0: run these and replace each {{NEXT_...}} below with the value returned.

SELECT COALESCE(MAX(Cfg_ID), 0) + 1 AS NEXT_CFG_ID FROM HSABC_DEV.ComplianceSourceFileConfig;
SELECT COALESCE(MAX(Req_ID), 0) + 1 AS NEXT_REQ_ID FROM HSABC_DEV.ComplianceRequestControl;
SELECT COALESCE(MAX(Load_ID), 0) + 1 AS NEXT_LOAD_ID FROM HSABC_DEV.ComplianceFileLoad;
SELECT COALESCE(MAX(Event_ID), 0) + 1 AS NEXT_EVENT_ID FROM HSABC_DEV.CMS_ComplianceExceptionsAudit;

-- Step 2.1: back up. CREATE TABLE AS cannot copy a table with an identity column, so the four tables with an ID
-- column get a plain backup table (the ID as an ordinary BIGINT, other columns nullable) and an INSERT ... SELECT.
CREATE MULTISET TABLE HSABC_DEV.ComplianceRunType_BKP AS (SELECT * FROM HSABC_DEV.ComplianceRunType) WITH DATA;
CREATE MULTISET TABLE HSABC_DEV.ComplianceDataSetSourceXwalk_BKP AS (SELECT * FROM HSABC_DEV.ComplianceDataSetSourceXwalk) WITH DATA;
CREATE MULTISET TABLE HSABC_DEV.ComplianceSourceFileConfig_BKP (
  Cfg_ID                 BIGINT NOT NULL,
  Project_Cd             VARCHAR(30),
  Table_Nm               VARCHAR(63),
  Src_ID                 VARCHAR(30),
  Src_File_Nm_Tmplt      VARCHAR(255),
  File_Vrsn_Cd           VARCHAR(30),
  Delmtr_Cd              VARCHAR(10),
  Src_File_Has_Hdr_Ind   SMALLINT,
  Src_File_Has_Trlr_Ind  SMALLINT,
  Allow_Zero_Rcd_Ind     SMALLINT,
  Src_File_Path          VARCHAR(500),
  Stg_Schema_Nm          VARCHAR(63),
  Stg_Table_Nm           VARCHAR(63),
  Core_Schema_Nm         VARCHAR(63),
  Core_Table_Nm          VARCHAR(63),
  Active_Ind             SMALLINT     DEFAULT 1,
  Load_Dtts           TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
  LoadUser_ID             VARCHAR(100) DEFAULT USER,
  LastUpdt_Dtts           TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
  LastUpdt_ID             VARCHAR(100) DEFAULT USER
)
UNIQUE PRIMARY INDEX (Cfg_ID);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig_BKP (Cfg_ID, Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, File_Vrsn_Cd, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID)
SELECT Cfg_ID, Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, File_Vrsn_Cd, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID FROM HSABC_DEV.ComplianceSourceFileConfig;
CREATE MULTISET TABLE HSABC_DEV.ComplianceRequestControl_BKP (
  Req_ID           BIGINT NOT NULL,
  Project_Cd       VARCHAR(30),
  Table_Nm         VARCHAR(63),
  Src_ID           VARCHAR(30),
  Run_Ty           VARCHAR(20),
  Cmplnc_Vrsn      VARCHAR(10),
  Rpt_Start_Dt_Key DATE,
  Rpt_End_Dt_Key   DATE,
  Req_Dt_Key       DATE,
  Btch_ID          VARCHAR(250),
  Req_Stat         VARCHAR(30),
  Resolution_Ty    VARCHAR(15),
  Reuse_Btch_ID    VARCHAR(250),
  Batch_Close_Ind  SMALLINT     DEFAULT 0,
  Load_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
  LastUpdt_Dtts     TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6)
)
UNIQUE PRIMARY INDEX (Req_ID);
INSERT INTO HSABC_DEV.ComplianceRequestControl_BKP (Req_ID, Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Req_Stat, Resolution_Ty, Reuse_Btch_ID, Batch_Close_Ind, Load_Dtts, LastUpdt_Dtts)
SELECT Req_ID, Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Req_Stat, Resolution_Ty, Reuse_Btch_ID, Batch_Close_Ind, Load_Dtts, LastUpdt_Dtts FROM HSABC_DEV.ComplianceRequestControl;
CREATE MULTISET TABLE HSABC_DEV.ComplianceFileLoad_BKP (
  Load_ID           BIGINT NOT NULL,
  Cfg_ID            BIGINT,
  Req_ID            BIGINT,
  Btch_ID           VARCHAR(250),
  File_Hash         CHAR(64),
  File_Share        VARCHAR(200),
  File_Path         VARCHAR(1024),
  File_Version      VARCHAR(200),
  File_Size_Byte    BIGINT,
  File_Sha256       CHAR(64),
  Load_Stat         VARCHAR(25),
  Rules_Stat        VARCHAR(25)   DEFAULT 'NOT_RUN',
  Reject_Rsn_Cd     VARCHAR(60),
  Stg_Rcd_Cnt       BIGINT,
  Error_Txt         VARCHAR(4000),
  Load_Dtts      TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6),
  LastUpdt_Dtts      TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6)
)
UNIQUE PRIMARY INDEX (Load_ID);
INSERT INTO HSABC_DEV.ComplianceFileLoad_BKP (Load_ID, Cfg_ID, Req_ID, Btch_ID, File_Hash, File_Share, File_Path, File_Version, File_Size_Byte, File_Sha256, Load_Stat, Rules_Stat, Reject_Rsn_Cd, Stg_Rcd_Cnt, Error_Txt, Load_Dtts, LastUpdt_Dtts)
SELECT Load_ID, Cfg_ID, Req_ID, Btch_ID, File_Hash, File_Share, File_Path, File_Version, File_Size_Byte, File_Sha256, Load_Stat, Rules_Stat, Reject_Rsn_Cd, Stg_Rcd_Cnt, Error_Txt, Load_Dtts, LastUpdt_Dtts FROM HSABC_DEV.ComplianceFileLoad;
CREATE MULTISET TABLE HSABC_DEV.CMS_ComplianceExceptionsAudit_BKP (
  Event_ID     BIGINT NOT NULL,
  Event_Ty     VARCHAR(60),
  Sevrty       VARCHAR(10),
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
  Actor        VARCHAR(100),
  Event_Txt    VARCHAR(4000),
  Notified_Ind SMALLINT      DEFAULT 0,
  Event_Dtts   TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6)
)
UNIQUE PRIMARY INDEX (Event_ID);
INSERT INTO HSABC_DEV.CMS_ComplianceExceptionsAudit_BKP (Event_ID, Event_Ty, Sevrty, Project_Cd, Table_Nm, Src_ID, Run_Ty, Req_ID, Load_ID, Ovrd_ID, Intake_ID, Btch_ID, File_Ref, Actor, Event_Txt, Notified_Ind, Event_Dtts)
SELECT Event_ID, Event_Ty, Sevrty, Project_Cd, Table_Nm, Src_ID, Run_Ty, Req_ID, Load_ID, Ovrd_ID, Intake_ID, Btch_ID, File_Ref, Actor, Event_Txt, Notified_Ind, Event_Dtts FROM HSABC_DEV.CMS_ComplianceExceptionsAudit;

-- Step 2.2: drop and create in the new order
DROP TABLE HSABC_DEV.ComplianceRunType;
CREATE MULTISET TABLE HSABC_DEV.ComplianceRunType (
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

DROP TABLE HSABC_DEV.ComplianceDataSetSourceXwalk;
CREATE MULTISET TABLE HSABC_DEV.ComplianceDataSetSourceXwalk (
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

DROP TABLE HSABC_DEV.ComplianceSourceFileConfig;
CREATE MULTISET TABLE HSABC_DEV.ComplianceSourceFileConfig (
  Cfg_ID                 BIGINT GENERATED BY DEFAULT AS IDENTITY (START WITH {{NEXT_CFG_ID}} INCREMENT BY 1 NO CYCLE),
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

DROP TABLE HSABC_DEV.ComplianceRequestControl;
CREATE MULTISET TABLE HSABC_DEV.ComplianceRequestControl (
  Req_ID           BIGINT GENERATED BY DEFAULT AS IDENTITY (START WITH {{NEXT_REQ_ID}} INCREMENT BY 1 NO CYCLE),
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

DROP TABLE HSABC_DEV.ComplianceFileLoad;
CREATE MULTISET TABLE HSABC_DEV.ComplianceFileLoad (
  Load_ID           BIGINT GENERATED BY DEFAULT AS IDENTITY (START WITH {{NEXT_LOAD_ID}} INCREMENT BY 1 NO CYCLE),
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

DROP TABLE HSABC_DEV.CMS_ComplianceExceptionsAudit;
CREATE MULTISET TABLE HSABC_DEV.CMS_ComplianceExceptionsAudit (
  Event_ID     BIGINT GENERATED BY DEFAULT AS IDENTITY (START WITH {{NEXT_EVENT_ID}} INCREMENT BY 1 NO CYCLE),
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

-- Step 2.3: copy the rows back
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Batch_Schedule_Sql_Txt, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID)
SELECT Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Batch_Schedule_Sql_Txt, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID FROM HSABC_DEV.ComplianceRunType_BKP;
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Period_Cd, Rpt_Dt_Sql_Txt, Include_Ind, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID)
SELECT Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Period_Cd, Rpt_Dt_Sql_Txt, Include_Ind, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID FROM HSABC_DEV.ComplianceDataSetSourceXwalk_BKP;
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Cfg_ID, Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, File_Vrsn_Cd, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID)
SELECT Cfg_ID, Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, File_Vrsn_Cd, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind, Load_Dtts, LoadUser_ID, LastUpdt_Dtts, LastUpdt_ID FROM HSABC_DEV.ComplianceSourceFileConfig_BKP;
INSERT INTO HSABC_DEV.ComplianceRequestControl (Req_ID, Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Req_Stat, Resolution_Ty, Reuse_Btch_ID, Batch_Close_Ind, Load_Dtts, LastUpdt_Dtts)
SELECT Req_ID, Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key, Btch_ID, Req_Stat, Resolution_Ty, Reuse_Btch_ID, Batch_Close_Ind, Load_Dtts, LastUpdt_Dtts FROM HSABC_DEV.ComplianceRequestControl_BKP;
INSERT INTO HSABC_DEV.ComplianceFileLoad (Load_ID, Cfg_ID, Req_ID, Btch_ID, File_Hash, File_Share, File_Path, File_Version, File_Size_Byte, File_Sha256, Load_Stat, Rules_Stat, Reject_Rsn_Cd, Stg_Rcd_Cnt, Error_Txt, Load_Dtts, LastUpdt_Dtts)
SELECT Load_ID, Cfg_ID, Req_ID, Btch_ID, File_Hash, File_Share, File_Path, File_Version, File_Size_Byte, File_Sha256, Load_Stat, Rules_Stat, Reject_Rsn_Cd, Stg_Rcd_Cnt, Error_Txt, Load_Dtts, LastUpdt_Dtts FROM HSABC_DEV.ComplianceFileLoad_BKP;
INSERT INTO HSABC_DEV.CMS_ComplianceExceptionsAudit (Event_ID, Event_Ty, Sevrty, Project_Cd, Table_Nm, Src_ID, Run_Ty, Req_ID, Load_ID, Ovrd_ID, Intake_ID, Btch_ID, File_Ref, Actor, Event_Txt, Notified_Ind, Event_Dtts)
SELECT Event_ID, Event_Ty, Sevrty, Project_Cd, Table_Nm, Src_ID, Run_Ty, Req_ID, Load_ID, Ovrd_ID, Intake_ID, Btch_ID, File_Ref, Actor, Event_Txt, Notified_Ind, Event_Dtts FROM HSABC_DEV.CMS_ComplianceExceptionsAudit_BKP;

-- Step 2.4: each row must show the same two counts; only then drop the backups
SELECT 'ComplianceRunType' AS Table_Nm, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceRunType) AS New_Cnt, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceRunType_BKP) AS Bkp_Cnt FROM (SELECT 1 AS x) d
UNION ALL
SELECT 'ComplianceDataSetSourceXwalk' AS Table_Nm, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceDataSetSourceXwalk) AS New_Cnt, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceDataSetSourceXwalk_BKP) AS Bkp_Cnt FROM (SELECT 1 AS x) d
UNION ALL
SELECT 'ComplianceSourceFileConfig' AS Table_Nm, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceSourceFileConfig) AS New_Cnt, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceSourceFileConfig_BKP) AS Bkp_Cnt FROM (SELECT 1 AS x) d
UNION ALL
SELECT 'ComplianceRequestControl' AS Table_Nm, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceRequestControl) AS New_Cnt, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceRequestControl_BKP) AS Bkp_Cnt FROM (SELECT 1 AS x) d
UNION ALL
SELECT 'ComplianceFileLoad' AS Table_Nm, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceFileLoad) AS New_Cnt, (SELECT COUNT(*) FROM HSABC_DEV.ComplianceFileLoad_BKP) AS Bkp_Cnt FROM (SELECT 1 AS x) d
UNION ALL
SELECT 'CMS_ComplianceExceptionsAudit' AS Table_Nm, (SELECT COUNT(*) FROM HSABC_DEV.CMS_ComplianceExceptionsAudit) AS New_Cnt, (SELECT COUNT(*) FROM HSABC_DEV.CMS_ComplianceExceptionsAudit_BKP) AS Bkp_Cnt FROM (SELECT 1 AS x) d;

DROP TABLE HSABC_DEV.ComplianceRunType_BKP;
DROP TABLE HSABC_DEV.ComplianceDataSetSourceXwalk_BKP;
DROP TABLE HSABC_DEV.ComplianceSourceFileConfig_BKP;
DROP TABLE HSABC_DEV.ComplianceRequestControl_BKP;
DROP TABLE HSABC_DEV.ComplianceFileLoad_BKP;
DROP TABLE HSABC_DEV.CMS_ComplianceExceptionsAudit_BKP;
