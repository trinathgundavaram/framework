-- CMSUNIV project metadata (fresh install). Metadata database: HSABC_DEV (replace for other environments).
-- Order: run the sections top to bottom. Then run validate-config (admin DAG).
-- Still to come (needs details): ComplianceSourceFileConfig (file sources), ComplianceInternalSourceConfig
-- (sources 10, 11, 40, 210), ComplianceTargetColumnConfig, ComplianceFileCheckConfig, ComplianceExportConfig,
-- ComplianceRuleBinding, ComplianceTable (table groups).

-- ----------------------------------------------------------------------------------------------------
-- 1. Project
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceProject (Project_Cd, Project_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CMS Universe submissions', 1);

-- ----------------------------------------------------------------------------------------------------
-- 2. Sources (all 55, a source without a crosswalk row gets no batches)
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('1', 'GTS (Grievances)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('2', 'JARVIS (PartD_CD)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('3', 'PARC (Appeals_PartC)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('4', 'CCMS (Part C ODs)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('6', 'OnBase (PartD Appeals)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('8', 'APAG (Appeals and Grievances)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('9', 'Service Coordinator (Call Log)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('10', 'QNXT', 'INTERNAL', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('11', 'DatamartSNPMOC', 'INTERNAL', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('30', 'Leon', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('32', 'Convey', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('40', 'AZ', 'INTERNAL', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('51', 'MedHOK', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('64', 'ESI', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('101', 'ADVOCATE', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('102', 'AllMed', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('103', 'ASH', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('104', 'Avidity', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('105', 'BeaconHealthOptions', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('106', 'CBH', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('107', 'CDH', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('108', 'DeltaDental', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('109', 'DentaQuest', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('110', 'Evicore', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('111', 'CignaAZ', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('112', 'myNexus', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('113', 'Navihealth', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('114', 'NewCenturyHealth', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('115', 'PHCN', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('116', 'ProspectMedicalSystems', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('117', 'SuperiorVision', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('118', 'Swedish', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('119', 'UOC', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('120', 'WellMed', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('121', 'HCS', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('123', 'Access2Care', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('124', 'EviCore Medical Oncology', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('130', 'Optum', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('131', 'PharmMD', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('134', 'Evicore-RadiationTherapy', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('154', 'CDHAllow', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('167', 'Catasys', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('175', 'ASH Acupuncture (ASHAP)', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('203', 'EviCore MSK', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('204', 'CPE Table 1 Compliance Team', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('210', 'Facets - GBS (Cornerstone)', 'INTERNAL', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('213', 'EYEMED', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('217', 'CDHDHMO', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('218', 'EvicoreDME', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('219', 'EvicorePAC', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('229', 'UnoHealth', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('244', 'HCSC CMS Universe', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('302', 'Leon - JARVIS', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('901', 'Convey-Miramar', 'FDR', 1);
INSERT INTO HSABC_DEV.ComplianceSourceSystem (Src_ID, Src_Nm, Src_Ty, Active_Ind)
VALUES ('992', 'MAN', 'FDR', 1);

-- ----------------------------------------------------------------------------------------------------
-- 3. Run types
-- DAILY: every day, previous day, closes the same day (SLA 1).
-- WEEKLY: every Sunday, the 60 days ending the Saturday before, closes Friday (SLA 5). Internal sources only (crosswalk).
-- MNT: the Sunday before the 3rd Tuesday (MONTHLY TUE week 3, offset -2), previous calendar month, SLA 5.
-- IN1: ad hoc, created daily for SNPCC1 only (schedule DAILY), previous day, SLA 1. Never released, so never pulled
--      or exported, BATCH_CLOSE closes it the next day as DATA_NOT_PROVIDED.
-- CMS: ad hoc, from ComplianceRequestInTake only, SLA 1.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('DAILY', 'Daily, previous day', 'SCHEDULED', 1, 0, 'DAILY', NULL, NULL, NULL, 1, 0, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('WEEKLY', 'Weekly on Sunday, 60 days ending the Saturday before', 'SCHEDULED', 5, 0, 'WEEKLY', 'SUN', NULL, NULL, 1, 0, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('MNT', 'Monthly on the Sunday before the 3rd Tuesday, previous month', 'SCHEDULED', 5, 0, 'MONTHLY', 'TUE', '3', NULL, 1, -2, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('IN1', 'Ad hoc daily batch for SNPCC1, previous day (closed without data)', 'ADHOC', 1, 0, 'DAILY', NULL, NULL, NULL, 1, 0, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('CMS', 'CMS ad-hoc request (ComplianceRequestInTake)', 'ADHOC', 1, 1, NULL, NULL, NULL, NULL, 1, 0, 1);

-- ----------------------------------------------------------------------------------------------------
-- 3a. Report dates per run type
-- Project default ('*') for DAILY / WEEKLY / MNT, IN1 for SNPCC1, the FA tables have their own dates for every
-- run type they get. FA2 - FA4 move to the new year's dates on February 1 (January keeps the older dates).
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', '*', 'DAILY', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', '*', 'WEEKLY', 'RANGE(DAY,60,60)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', '*', 'MNT', 'PREV_CALENDAR_MONTH', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', 'IN1', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'DAILY', 'LAST_DAYS(MONTH,1,14)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'MNT', 'LAST_DAYS(MONTH,1,14)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'DAILY', 'ANNUAL_WINDOW(01-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'MNT', 'ANNUAL_WINDOW(01-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'DAILY', 'ANNUAL_WINDOW(11-01,12-31,02-01)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'MNT', 'ANNUAL_WINDOW(11-01,12-31,02-01)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'DAILY', 'ANNUAL_WINDOW(11-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'WEEKLY', 'ANNUAL_WINDOW(11-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'MNT', 'ANNUAL_WINDOW(11-01,01-31)', 1);

-- ----------------------------------------------------------------------------------------------------
-- 4. Crosswalk: one row per table and source
-- FDR sources: DAILY, MNT, CMS. Internal sources: DAILY, WEEKLY, MNT, CMS (SNPCC1 / 11 also IN1).
-- Version 2200 from 1900-01-01.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '101', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '103', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '110', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '120', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '124', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '134', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '203', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '218', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '219', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '10', 'DAILY,WEEKLY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '40', 'DAILY,WEEKLY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '210', 'DAILY,WEEKLY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '101', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '103', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '106', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '107', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '120', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '121', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '154', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '175', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '213', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '217', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '32', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '51', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA1', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA2', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA3', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '10', 'DAILY,WEEKLY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '40', 'DAILY,WEEKLY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '210', 'DAILY,WEEKLY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '32', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '64', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '901', 'DAILY,MNT,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', '11', 'DAILY,WEEKLY,MNT,IN1,CMS', DATE '1900-01-01', NULL, '2200', 1, 1);
