-- UMCM (Clinical Auth Sample Auditing) metadata for DEV: ODAG1 from MedHOK (51), loaded to the export feed by
-- HSPROCS_$ENV.sp_OSS_DA_PROV_TO_EXPORT_LOAD_UMCM. Needs framework 0.3.78 or later.

-- ----------------------------------------------------------------------------------------------------
-- 1. Project
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceProject (Project_Cd, Project_Desc, Active_Ind)
VALUES ('UMCM', 'Clinical Auth Sample Auditing', 1);

-- ----------------------------------------------------------------------------------------------------
-- 2. Run types (shared by every project: DLY and WKLY exist already)
-- MNTH: the 3rd of every month, previous calendar month, SLA 3.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('MNTH', 'Monthly on the 3rd, previous calendar month', 'SCHEDULED', 3, 0, 'MONTHLY', '3', NULL, NULL, 1, 0, 1);

-- ----------------------------------------------------------------------------------------------------
-- 3. Report dates per run type
-- DLY: the previous day. WKLY (created Sunday): last Sunday to Saturday. MNTH: the previous calendar month.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('UMCM', '*', 'DLY', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('UMCM', '*', 'WKLY', 'RANGE(DAY,7,7)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('UMCM', '*', 'MNTH', 'PREV_CALENDAR_MONTH', 1);

-- ----------------------------------------------------------------------------------------------------
-- 4. Crosswalk
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('UMCM', 'ODAG1', '51', 'DLY,WKLY,MNTH', DATE '1900-01-01', NULL, '2200', 1, 1);

-- ----------------------------------------------------------------------------------------------------
-- 5. File source: UMCM's own inbound folder; staging and core shared with CMSUNIV ODAG1 for now
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('UMCM', 'ODAG1', '51', 'CMSAUTH_MHK_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/UMCM', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);

-- ----------------------------------------------------------------------------------------------------
-- 6. File checks: the MedHOK layout, as for CMSUNIV source 51
-- No header name check; the file fields in order (with MedHOK's four trailing fields); the trailer line.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('UMCM', 'ODAG1', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('UMCM', 'ODAG1', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AORReceiptDate|AORReceiptTime|RequestDetermination|WasTheRequestProcessedAsStdOrExpt|Wasatimeframeextensiontaken|DateOfDetermination|TimeOfDetermination|DateoralnotificationprovidedtoMember|TimeoralnotificationprovidedtoMember|DateWrittenNotificationprovidedToMember|TimeWrittenNotificationprovidedToMember|Whomadetherequest|Issuedescriptionandtypeofservice|WasAnExptRequestMadeButProcessedAsStd|WasTheRequestDeniedForLackOfMedicalNecessity|SourceUnique_ID|Revision|Filler1|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('UMCM', 'ODAG1', '51', 'TRAILER', '*', 'RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP', 1, 1);

-- ----------------------------------------------------------------------------------------------------
-- 7. Columns the framework fills (staging and core)
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Src_Data_Key', 'SOURCE_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Source_ID', 'SOURCE_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Universe_Vrsn', 'VERSION', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Universe_Version', 'VERSION', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Run_Ty', 'RUN_TYPE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'FileName', 'FILE_NAME', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Load_Dtts', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'LastUpdt_Dtts', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'StagingCreateDate', 'LOAD_DATE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'AuditDataID', 'LOAD_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', '*', 'Batch_ID', 'BATCH_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('UMCM', 'ODAG1', 'ODAG_DE_Table1_2022_ID', 'ROW_SEQUENCE', NULL, 1);

-- ----------------------------------------------------------------------------------------------------
-- 8. Export feed: soft delete and reload of the batch and run type by the UMCM procedure
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Proc_Nm, Exp_Proc_Parm_Txt, Active_Ind)
VALUES ('UMCM', 'ODAG1', 'EXPORT_FEED_$ENV_T', 'SDO_CMPLNC_CLNCL_AUD_UM_AUTHZN_ODAG1', 'HSPROCS_$ENV.sp_OSS_DA_PROV_TO_EXPORT_LOAD_UMCM', '{btch_id}, {data_btch_id}, {run_ty}, MSG', 1);
