-- One-time upgrade to 0.3.82 of the DEV tables (replace HSABC_DEV). Renames only: no row, value, type, default
-- or index changes.
-- Part 1: the 20 framework tables renamed, Compliance<Name> -> GOVT_RGLTRY_<Name>
--         (CMS_ComplianceExceptionsAudit -> CMS_GOVT_RGLTRY_ExceptionsAudit).
-- Part 2: the column Cmplnc_Vrsn renamed to Rpt_Version in GOVT_RGLTRY_DataSetSourceXwalk,
--         GOVT_RGLTRY_RequestInTake and GOVT_RGLTRY_RequestControl.
-- Run once, with the DAGs paused, before deploying framework 0.3.82. Compare the row counts of step 0 and step 3.
-- Afterwards airflow_resources/sql/schema_check.sql should return no rows.

-- ---------------------------------------------------------------------------------------------------------------
-- Step 0: row counts before
SELECT 'ComplianceProject' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceProject
UNION ALL SELECT 'ComplianceTable' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceTable
UNION ALL SELECT 'ComplianceExportConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceExportConfig
UNION ALL SELECT 'ComplianceInternalSourceConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceInternalSourceConfig
UNION ALL SELECT 'ComplianceSourceSystem' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceSourceSystem
UNION ALL SELECT 'ComplianceRunType' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceRunType
UNION ALL SELECT 'ComplianceBatchCalendar' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceBatchCalendar
UNION ALL SELECT 'ComplianceRunTypePeriod' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceRunTypePeriod
UNION ALL SELECT 'ComplianceDataSetSourceXwalk' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceDataSetSourceXwalk
UNION ALL SELECT 'ComplianceSourceFileConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceSourceFileConfig
UNION ALL SELECT 'ComplianceFileCheckConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceFileCheckConfig
UNION ALL SELECT 'ComplianceTargetColumnConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceTargetColumnConfig
UNION ALL SELECT 'ComplianceRuleBinding' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceRuleBinding
UNION ALL SELECT 'ComplianceRequestInTake' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceRequestInTake
UNION ALL SELECT 'ComplianceRequestControl' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceRequestControl
UNION ALL SELECT 'ComplianceFileLoad' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceFileLoad
UNION ALL SELECT 'ComplianceBatchOverride' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceBatchOverride
UNION ALL SELECT 'ComplianceRequestFileDetail' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceRequestFileDetail
UNION ALL SELECT 'CMS_ComplianceExceptionsAudit' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.CMS_ComplianceExceptionsAudit
UNION ALL SELECT 'ComplianceLock' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.ComplianceLock
ORDER BY 1;

-- ---------------------------------------------------------------------------------------------------------------
-- Part 1: table renames
RENAME TABLE HSABC_DEV.ComplianceProject TO HSABC_DEV.GOVT_RGLTRY_Project;
RENAME TABLE HSABC_DEV.ComplianceTable TO HSABC_DEV.GOVT_RGLTRY_Table;
RENAME TABLE HSABC_DEV.ComplianceExportConfig TO HSABC_DEV.GOVT_RGLTRY_ExportConfig;
RENAME TABLE HSABC_DEV.ComplianceInternalSourceConfig TO HSABC_DEV.GOVT_RGLTRY_InternalSourceConfig;
RENAME TABLE HSABC_DEV.ComplianceSourceSystem TO HSABC_DEV.GOVT_RGLTRY_SourceSystem;
RENAME TABLE HSABC_DEV.ComplianceRunType TO HSABC_DEV.GOVT_RGLTRY_RunType;
RENAME TABLE HSABC_DEV.ComplianceBatchCalendar TO HSABC_DEV.GOVT_RGLTRY_BatchCalendar;
RENAME TABLE HSABC_DEV.ComplianceRunTypePeriod TO HSABC_DEV.GOVT_RGLTRY_RunTypePeriod;
RENAME TABLE HSABC_DEV.ComplianceDataSetSourceXwalk TO HSABC_DEV.GOVT_RGLTRY_DataSetSourceXwalk;
RENAME TABLE HSABC_DEV.ComplianceSourceFileConfig TO HSABC_DEV.GOVT_RGLTRY_SourceFileConfig;
RENAME TABLE HSABC_DEV.ComplianceFileCheckConfig TO HSABC_DEV.GOVT_RGLTRY_FileCheckConfig;
RENAME TABLE HSABC_DEV.ComplianceTargetColumnConfig TO HSABC_DEV.GOVT_RGLTRY_TargetColumnConfig;
RENAME TABLE HSABC_DEV.ComplianceRuleBinding TO HSABC_DEV.GOVT_RGLTRY_RuleBinding;
RENAME TABLE HSABC_DEV.ComplianceRequestInTake TO HSABC_DEV.GOVT_RGLTRY_RequestInTake;
RENAME TABLE HSABC_DEV.ComplianceRequestControl TO HSABC_DEV.GOVT_RGLTRY_RequestControl;
RENAME TABLE HSABC_DEV.ComplianceFileLoad TO HSABC_DEV.GOVT_RGLTRY_FileLoad;
RENAME TABLE HSABC_DEV.ComplianceBatchOverride TO HSABC_DEV.GOVT_RGLTRY_BatchOverride;
RENAME TABLE HSABC_DEV.ComplianceRequestFileDetail TO HSABC_DEV.GOVT_RGLTRY_RequestFileDetail;
RENAME TABLE HSABC_DEV.CMS_ComplianceExceptionsAudit TO HSABC_DEV.CMS_GOVT_RGLTRY_ExceptionsAudit;
RENAME TABLE HSABC_DEV.ComplianceLock TO HSABC_DEV.GOVT_RGLTRY_Lock;

-- ---------------------------------------------------------------------------------------------------------------
-- Part 2: column renames
ALTER TABLE HSABC_DEV.GOVT_RGLTRY_DataSetSourceXwalk RENAME Cmplnc_Vrsn TO Rpt_Version;
ALTER TABLE HSABC_DEV.GOVT_RGLTRY_RequestInTake RENAME Cmplnc_Vrsn TO Rpt_Version;
ALTER TABLE HSABC_DEV.GOVT_RGLTRY_RequestControl RENAME Cmplnc_Vrsn TO Rpt_Version;

-- ---------------------------------------------------------------------------------------------------------------
-- Step 3: row counts after (the same numbers as step 0)
SELECT 'GOVT_RGLTRY_Project' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_Project
UNION ALL SELECT 'GOVT_RGLTRY_Table' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_Table
UNION ALL SELECT 'GOVT_RGLTRY_ExportConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_ExportConfig
UNION ALL SELECT 'GOVT_RGLTRY_InternalSourceConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_InternalSourceConfig
UNION ALL SELECT 'GOVT_RGLTRY_SourceSystem' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_SourceSystem
UNION ALL SELECT 'GOVT_RGLTRY_RunType' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_RunType
UNION ALL SELECT 'GOVT_RGLTRY_BatchCalendar' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_BatchCalendar
UNION ALL SELECT 'GOVT_RGLTRY_RunTypePeriod' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_RunTypePeriod
UNION ALL SELECT 'GOVT_RGLTRY_DataSetSourceXwalk' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_DataSetSourceXwalk
UNION ALL SELECT 'GOVT_RGLTRY_SourceFileConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_SourceFileConfig
UNION ALL SELECT 'GOVT_RGLTRY_FileCheckConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_FileCheckConfig
UNION ALL SELECT 'GOVT_RGLTRY_TargetColumnConfig' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_TargetColumnConfig
UNION ALL SELECT 'GOVT_RGLTRY_RuleBinding' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_RuleBinding
UNION ALL SELECT 'GOVT_RGLTRY_RequestInTake' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_RequestInTake
UNION ALL SELECT 'GOVT_RGLTRY_RequestControl' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_RequestControl
UNION ALL SELECT 'GOVT_RGLTRY_FileLoad' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_FileLoad
UNION ALL SELECT 'GOVT_RGLTRY_BatchOverride' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_BatchOverride
UNION ALL SELECT 'GOVT_RGLTRY_RequestFileDetail' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_RequestFileDetail
UNION ALL SELECT 'CMS_GOVT_RGLTRY_ExceptionsAudit' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.CMS_GOVT_RGLTRY_ExceptionsAudit
UNION ALL SELECT 'GOVT_RGLTRY_Lock' AS Table_Nm, COUNT(*) AS Row_Cnt FROM HSABC_DEV.GOVT_RGLTRY_Lock
ORDER BY 1;
