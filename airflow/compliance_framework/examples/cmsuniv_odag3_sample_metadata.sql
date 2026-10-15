-- Sample metadata for project CMSUNIV, table ODAG3 (Teradata).
-- The same rows as cmsuniv_odag3_sample_metadata.xlsx, one INSERT per row.
--
-- Before running: replace HSABC_DEV with your metadata database, and the sample values (source names, schemas,
-- folders, the rule group, the header names) with the real ones. Run the sections in this order.
-- Identity columns (Cfg_ID, Intake_ID, Ovrd_ID) and the audit columns (Created_Dtts, Created_By, Updated_Dtts,
-- Updated_By) are left out: the database fills them.


-- ----------------------------------------------------------------------------------------------------
-- 1. The project
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceProject
  (Project_Cd, Project_Desc, Active_Ind)
VALUES
  ('CMSUNIV', 'CMS Universe submissions', 1);


-- ----------------------------------------------------------------------------------------------------
-- 1a. Optional: the table group of each table
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceTable
  (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', 'ODAG', 'ODAG table 3', 1);


-- ----------------------------------------------------------------------------------------------------
-- 2. Sources: three FDR sources, two internal sources with files, two internal sources pulled with a query
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('51', 'MedHOK', 'FDR', 1);

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('130', 'OptumRx', 'FDR', 1);

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('140', 'ESI', 'FDR', 1);

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('10', 'Claims', 'INTERNAL', 1);

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('20', 'Appeals', 'INTERNAL', 1);

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('30', 'UMAuth', 'INTERNAL', 1);

INSERT INTO HSABC_DEV.ComplianceSourceSystem
  (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES
  ('40', 'AppealsDB', 'INTERNAL', 1);


-- ----------------------------------------------------------------------------------------------------
-- 3. Run types. Batch_Schedule_Sql_Txt says when scheduled batches are created
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceRunType
  (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Batch_Schedule_Sql_Txt, Active_Ind)
VALUES
  ('DAILY', 'Daily monitoring for the previous day', 'SCHEDULED', 1, 0, 'SELECT 1 AS due', 1);

INSERT INTO HSABC_DEV.ComplianceRunType
  (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Batch_Schedule_Sql_Txt, Active_Ind)
VALUES
  ('MNT', 'Monthly submission, created in the week of the 3rd Tuesday', 'SCHEDULED', 10, 0, 'SELECT tue AS period_dt
FROM (SELECT CAST({run_date} AS DATE) + (3 - TD_DAY_OF_WEEK(CAST({run_date} AS DATE))) AS tue) x
WHERE TD_DAY_OF_WEEK(CAST({run_date} AS DATE)) <= 5
  AND (EXTRACT(DAY FROM tue) - 1) / 7 + 1 = 3', 1);

INSERT INTO HSABC_DEV.ComplianceRunType
  (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Batch_Schedule_Sql_Txt, Active_Ind)
VALUES
  ('CMS', 'CMS ad-hoc request (through ComplianceRequestInTake)', 'ADHOC', 5, 1, NULL, 1);


-- ----------------------------------------------------------------------------------------------------
-- 4. Which source gets which run type. Rpt_Dt_Sql_Txt gives the report dates of a scheduled batch
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '10', 'DAILY', DATE '2026-01-01', NULL, '2200', 'SELECT CAST({run_date} AS DATE) - 1 AS rpt_start, CAST({run_date} AS DATE) - 1 AS rpt_end', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '10', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '10', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '20', 'DAILY', DATE '2026-01-01', NULL, '2200', 'SELECT CAST({run_date} AS DATE) - 1 AS rpt_start, CAST({run_date} AS DATE) - 1 AS rpt_end', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '20', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '20', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '30', 'DAILY', DATE '2026-01-01', NULL, '2200', 'SELECT CAST({run_date} AS DATE) - 1 AS rpt_start, CAST({run_date} AS DATE) - 1 AS rpt_end', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '30', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '30', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '40', 'DAILY', DATE '2026-01-01', NULL, '2200', 'SELECT CAST({run_date} AS DATE) - 1 AS rpt_start, CAST({run_date} AS DATE) - 1 AS rpt_end', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '40', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '40', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '51', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '51', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '130', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '130', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '140', 'MNT', DATE '2026-01-01', NULL, '2200', 'SELECT ADD_MONTHS(p - EXTRACT(DAY FROM p) + 1, -1) AS rpt_start, p - EXTRACT(DAY FROM p) AS rpt_end FROM (SELECT CAST({period_dt} AS DATE) AS p) d', 1);

INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Rpt_Dt_Sql_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '140', 'CMS', DATE '2026-01-01', NULL, '2200', NULL, 1);


-- ----------------------------------------------------------------------------------------------------
-- 5. One row per file source: file name, delimiter, folder, staging and core table
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '51', 'CMSAuth_MedHOK_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, '\\nas01\Compliance\$env\cmsuniv\FDR\PYMT_C', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);

INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '130', 'CMSAuth_OptumRx_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, '\\nas01\Compliance\$env\cmsuniv\FDR\PYMT_C', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);

INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '140', 'CMSAuth_ESI_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, '\\nas01\Compliance\$env\cmsuniv\FDR\PYMT_C', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);

INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '10', 'CMSAuth_Claims_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 0, 1, '\\nas01\Compliance\$env\cmsuniv\internal\PYMT_C', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);

INSERT INTO HSABC_DEV.ComplianceSourceFileConfig
  (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '20', 'CMSAuth_Appeals_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 0, 1, '\\nas01\Compliance\$env\cmsuniv\internal\PYMT_C', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);


-- ----------------------------------------------------------------------------------------------------
-- 5a. Optional: internal sources pulled with a query (SOURCE_LOAD); Src_Conn_Cd empty = same Teradata
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceSourceQueryConfig
  (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '30', 'TERADATA', NULL, 'SELECT a.Mbr_First_Nm AS MemberFirstName, a.Mbr_Last_Nm AS MemberLastName, a.Mbr_ID AS MemberID, a.Contract_ID AS ContractID, a.Auth_Nbr AS AuthorizationorClaimNumber, a.Rcvd_Dt AS Datetherequestwasreceived FROM CMSUNIV_SRC_DEV_V.UM_AUTH a WHERE a.Btch_ID = {btch_id}', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', 1);

INSERT INTO HSABC_DEV.ComplianceSourceQueryConfig
  (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '40', 'SQLSERVER', 'cms_src_appeals_sqlsrv', 'SELECT m.FirstName AS MemberFirstName, m.LastName AS MemberLastName, m.MemberNumber AS MemberID, a.ContractId AS ContractID, a.AppealNumber AS AuthorizationorClaimNumber, CONVERT(varchar(10), a.ReceivedDate, 23) AS Datetherequestwasreceived FROM dbo.Appeal a JOIN dbo.Member m ON m.MemberKey = a.MemberKey WHERE a.Btch_ID = {btch_id}', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', 1);


-- ----------------------------------------------------------------------------------------------------
-- 6. The staging / core columns the framework fills; every other staging column is a file column
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', '*', 'StagingCreateDate', 'LOAD_TIMESTAMP', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', '*', 'Universe_Vrsn', 'VERSION', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', '*', 'Run_Ty', 'RUN_TYPE', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', '*', 'FileName', 'FILE_NAME', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', '*', 'Src_Data_Key', 'SOURCE_ID', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', '*', 'Load_Dtts', 'LOAD_TIMESTAMP', NULL, 1);

INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig
  (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', 'ODAG_DE_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);


-- ----------------------------------------------------------------------------------------------------
-- 6a. Optional: the export table of a core table (EXPORT_LOAD)
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceExportConfig
  (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Exp_Proc_Nm, Exp_Proc_Parm_Txt, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', 'CMSUNIV_EXPORT_$ENV_T', 'ODAG_DE_TABLE3_2022_EXP', NULL, NULL, NULL, 1);

-- The export table: the core table's columns plus Exp_Btch_ID (the export batch), for example:
--   CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG_DE_TABLE3_2022_EXP AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE3_2022 WITH NO DATA;
--   ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG_DE_TABLE3_2022_EXP ADD Exp_Btch_ID VARCHAR(100);
--
-- Optional: export through a procedure instead of the framework's delete and INSERT ... SELECT. The procedure deletes
-- and inserts the batch's export rows itself; Exp_Proc_Parm_Txt lists its parameters in order ({btch_id},
-- {data_btch_id}, {exp_btch_id}, {reused_btch_ids}, {project_cd}, {table_nm}, {src_id}, {run_ty}, {version},
-- {req_id}, {req_dt}, {rpt_start}, {rpt_end}; empty = {btch_id}, {data_btch_id}, {exp_btch_id}):
--   UPDATE HSABC_DEV.ComplianceExportConfig
--      SET Exp_Proc_Nm = 'CMSUNIV_EXPORT_$ENV_T.SP_EXPORT_ODAG3',
--          Exp_Proc_Parm_Txt = '{btch_id}, {data_btch_id}, {exp_btch_id}, {reused_btch_ids}'
--    WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG3';


-- ----------------------------------------------------------------------------------------------------
-- 7. The checks FILE_CHECK applies. Include_Ind = 0 excludes the files the row covers
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '*', 'HEADER_NAMES', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|AORReceiptDate|WaiverofLiabilityReceiptDate|WasItaCleanClaim|WasTheRequestProcessedasanODorRecon|RequestDetermination|DateofDetermination|DateClaimReconsiderationwasPaid|DateWrittenNotificationprovidedToMember|DateWrittenNotificationProvidedtoProvider|DateForwardedtoIRE|Whomadetherequest|Issuedescriptionandtypeofservice|WasTheInitOrgDetReqtDeniedForLackOfMediNecessity|ExRMNumber|SourceUnique_ID|Filler2|Filler3', 1, 1);

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '*', 'RECORD_TYPE', 'H,D,T', 1, 1);

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '*', 'TRAILER', 'RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP', 1, 1);

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', '*', '*', 'CHARACTER_SET', 'latin-1', 1, 1);

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', '*', '*', 'COLUMN_VALUES', 'ALL', 1, 1);

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '10', 'RECORD_TYPE', NULL, 0, 1);

INSERT INTO HSABC_DEV.ComplianceFileCheckConfig
  (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '20', 'RECORD_TYPE', NULL, 0, 1);


-- ----------------------------------------------------------------------------------------------------
-- 8. Optional: GRE rules for FILE_RULES
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceRuleBinding
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Gre_Rule_Group, Gre_Rule_Variant, Active_Ind)
VALUES
  ('CMSUNIV', 'ODAG3', '*', '*', 'CMSUNIV_ODAG3', 'DEFAULT', 1);


-- ----------------------------------------------------------------------------------------------------
-- 9. Ad-hoc (CMS) requests. One row per request
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceRequestInTake
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
VALUES
  ('CMSUNIV', 'ODAG3', NULL, 'CMS', DATE '2026-07-01', DATE '2026-09-30', DATE '2026-10-19', DATE '2026-10-19');

INSERT INTO HSABC_DEV.ComplianceRequestInTake
  (Project_Cd, Table_Nm, Src_ID, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
VALUES
  ('CMSUNIV', 'ODAG3', '140', 'CMS', DATE '2026-04-01', DATE '2026-06-30', DATE '2026-10-21', DATE '2026-10-21');


-- ----------------------------------------------------------------------------------------------------
-- 10. Only for a carry-forward, and only after the batches exist: an approved REUSE override.
--     The batch that reuses is found by its Btch_ID; Reuse_Btch_ID is the earlier batch whose data is reused.
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceBatchOverride
  (Override_Ty, Req_ID, Btch_ID, Reuse_Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'REUSE', Req_ID, Btch_ID, '20261019_CMSUNIV_ODAG3_140_CMS_2200_1', 'APPROVED', DATE '2026-10-30',
       'ESI confirmed no change since the previous CMS request', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl
WHERE Btch_ID = '20261021_CMSUNIV_ODAG3_140_CMS_2200_1';


-- ----------------------------------------------------------------------------------------------------
-- 11. Only to open a closed batch again (e.g. for a late or corrected file): an approved REOPEN override.
--     OVERRIDE_DECISIONS applies it once; the batch stays open until new data arrives, then BATCH_CLOSE closes it.
--     (A LATE_ARRIVAL / CORRECTION override loads the file into the closed batch without reopening it.)
-- ----------------------------------------------------------------------------------------------------

INSERT INTO HSABC_DEV.ComplianceBatchOverride
  (Override_Ty, Req_ID, Btch_ID, Apprvl_Stat, Valid_Thru_Dt_Key, Rsn_Txt, Reviewed_By, Reviewed_Dtts)
SELECT 'REOPEN', Req_ID, Btch_ID, 'APPROVED', DATE '2026-10-31',
       'ESI resends corrected data for the closed CMS request', 'approver_id', CURRENT_TIMESTAMP(6)
FROM HSABC_DEV.ComplianceRequestControl
WHERE Btch_ID = '20261019_CMSUNIV_ODAG3_140_CMS_2200_1';
