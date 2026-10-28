-- CMSUNIV project metadata (fresh install). Metadata database: HSABC_DEV (replace for other environments).
-- Order: run the sections top to bottom. Then run validate-config from the command line
-- (python -m compliance_framework.cli validate-config). Tables: airflow_resources/sql/schema.sql (framework 0.3.69).
-- Facets (210) pulls for ODAG3 / FA4 read the Teradata views CMSUNIV_CORE_DEV_V.CSF_* by Batch_ID (Src_Sql_Txt has
-- no $ENV), which the upstream process is still to write (until then they find no rows).
-- Export: into the existing feed tables EXPORT_FEED_$ENV_T.SDO_CMSUNIV_<table>_2022 (section 8).
-- Data quality rules (FILE_RULES) need no rows here: each exported batch runs the GRE rule group named like its table
-- (ODAG1 ... SNPCC1) with the run type as variant; ComplianceRuleBinding only to name other groups.

-- ----------------------------------------------------------------------------------------------------
-- 1. Project
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceProject (Project_Cd, Project_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CMS Universe submissions', 1);

-- ----------------------------------------------------------------------------------------------------
-- 1a. Table groups (a step can run for one group: table_group ODAG / CDAG / FA / SNPCC)
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', 'ODAG', 'ODAG table 1', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', 'ODAG', 'ODAG table 2', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'ODAG', 'ODAG table 3', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', 'ODAG', 'ODAG table 4', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', 'ODAG', 'ODAG table 5', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', 'ODAG', 'ODAG table 6', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', 'CDAG', 'CDAG table 1', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', 'CDAG', 'CDAG table 2', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', 'CDAG', 'CDAG table 3', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', 'CDAG', 'CDAG table 4', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', 'CDAG', 'CDAG table 5', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', 'CDAG', 'CDAG table 6', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', 'CDAG', 'CDAG table 7', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'FA', 'FA table 1', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'FA', 'FA table 2', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'FA', 'FA table 3', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'FA', 'FA table 4', 1);
INSERT INTO HSABC_DEV.ComplianceTable (Project_Cd, Table_Nm, Table_Grp_Cd, Table_Desc, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', 'SNPCC', 'SNPCC table 1', 1);

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
-- DLY (daily): every day, previous day, closes the same day (SLA 1).
-- WKLY (weekly): every Sunday, the 60 days ending the Saturday before, closes Friday (SLA 5). Internal sources only (crosswalk).
-- MNT: the Sunday before the 3rd Tuesday (MONTHLY TUE week 3, offset -2), previous calendar month, SLA 5.
-- IN1: ad hoc, created daily for SNPCC1 only (schedule DAILY), previous day, SLA 1. Never released, so never pulled
--      or exported, BATCH_CLOSE closes it the next day as DATA_NOT_PROVIDED.
-- CMS: ad hoc, from ComplianceRequestInTake only (no crosswalk row: each request names its source and version), SLA 1.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('DLY', 'Daily, previous day', 'SCHEDULED', 1, 0, 'DAILY', NULL, NULL, NULL, 1, 0, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('WKLY', 'Weekly on Sunday, 60 days ending the Saturday before', 'SCHEDULED', 5, 0, 'WEEKLY', 'SUN', NULL, NULL, 1, 0, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('MNT', 'Monthly on the Sunday before the 3rd Tuesday, previous month', 'SCHEDULED', 5, 0, 'MONTHLY', 'TUE', '3', NULL, 1, -2, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('IN1', 'Ad hoc daily batch for SNPCC1, previous day (closed without data)', 'ADHOC', 1, 0, 'DAILY', NULL, NULL, NULL, 1, 0, 1);
INSERT INTO HSABC_DEV.ComplianceRunType (Run_Ty, Run_Ty_Desc, Run_Category_Cd, SLA_Days, Carry_Fwd_Ind, Sched_Cd, Sched_Day_Cd, Sched_Week_Cd, Sched_Months_Txt, Sched_Window_Days, Sched_Offset_Days, Active_Ind)
VALUES ('CMS', 'CMS ad-hoc request (ComplianceRequestInTake)', 'ADHOC', 1, 1, NULL, NULL, NULL, NULL, 1, 0, 1);

-- ----------------------------------------------------------------------------------------------------
-- 3a. Report dates per run type
-- Project default ('*') for DLY / WKLY / MNT, IN1 for SNPCC1, the FA tables have their own dates for every
-- run type they get. FA2 - FA4 move to the new year's dates on February 1 (January keeps the older dates).
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', '*', 'DLY', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', '*', 'WKLY', 'RANGE(DAY,60,60)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', '*', 'MNT', 'PREV_CALENDAR_MONTH', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', 'IN1', 'PREV_DAY', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'DLY', 'LAST_DAYS(MONTH,1,14)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'MNT', 'LAST_DAYS(MONTH,1,14)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'DLY', 'ANNUAL_WINDOW(01-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'MNT', 'ANNUAL_WINDOW(01-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'DLY', 'ANNUAL_WINDOW(11-01,12-31,02-01)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'MNT', 'ANNUAL_WINDOW(11-01,12-31,02-01)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'DLY', 'ANNUAL_WINDOW(11-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'WKLY', 'ANNUAL_WINDOW(11-01,01-31)', 1);
INSERT INTO HSABC_DEV.ComplianceRunTypePeriod (Project_Cd, Table_Nm, Run_Ty, Rpt_Period_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'MNT', 'ANNUAL_WINDOW(11-01,01-31)', 1);

-- ----------------------------------------------------------------------------------------------------
-- 4. Crosswalk: one row per table and source
-- FDR sources: DLY, MNT. Internal sources: DLY, WKLY, MNT (SNPCC1 / 11 also IN1, ad hoc with a schedule).
-- CMS (ad hoc) has no crosswalk rows: its batches come from ComplianceRequestInTake, one request per source.
-- Version 2200 from 1900-01-01.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '101', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '103', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '110', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '120', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '124', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '134', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '203', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '218', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '219', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '10', 'DLY,WKLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '40', 'DLY,WKLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '210', 'DLY,WKLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '101', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '103', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '106', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '107', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '120', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '121', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '154', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '175', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '213', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '217', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '32', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '51', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA1', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA2', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA3', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '10', 'DLY,WKLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '40', 'DLY,WKLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '210', 'DLY,WKLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '32', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '64', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '901', 'DLY,MNT', DATE '1900-01-01', NULL, '2200', 1, 1);
INSERT INTO HSABC_DEV.ComplianceDataSetSourceXwalk (Project_Cd, Table_Nm, Src_ID, Run_Ty, Effective_Start_Dt_Key, Effective_End_Dt_Key, Cmplnc_Vrsn, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', '11', 'DLY,WKLY,MNT,IN1', DATE '1900-01-01', NULL, '2200', 1, 1);

-- ----------------------------------------------------------------------------------------------------
-- 7. File checks: the header line of each table's FDR files
-- HEADER_NAMES = the header names in file order (compared without case and spaces). Applies to every source of
-- the table ('*'). As provided.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AOREquivalentnoticeReceiptDate|AOREquivalentnoticeReceiptTime|RequestDetermination|WastherequestprocessedasStandardorExpedited|Wasatimeframeextensiontaken|DateofDetermination|TimeofDetermination|Dateoralnotificationprovidedtoenrollee|Timeoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Whomadetherequest|Issuedescriptionandtypeofservice|Wasanexpeditedrequestmadebutprocessedasstandard|Wastherequestdeniedforlackofmedicalnecessity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AOREquivalentNoticeReceiptDate|AOREquivalentNoticeReceiptTime|RequestDetermination|WastherequestprocessedasStandardorExpedited|Wasatimeframeextensiontaken|DateofDetermination|TimeofDetermination|Dateoralnotificationprovidedtoenrollee|Timeoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Datereconsidereddeterminationeffectuatedinthesystem|Timereconsidereddeterminationeffectuatedinthesystem|DateforwardedtoIRE|TimeforwardedtoIRE|Whomadetherequest|Issuedescriptionandtypeofservice|Wasanexpeditedrequestmadebutprocessedasstandard|Wastheinitialorganizationdeterminationrequestdeniedforlackofmedicalnecessity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamAndRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|AOREquivalentnoticeReceiptDate|WaiverofLiabilityWOLReceiptDate|Wasitacleanclaim|WastherequestprocessedasanODorRecon|RequestDetermination|DateofDetermination|Dateclaimreconsiderationwaspaid|Datewrittennotificationprovidedtoenrollee|Datewrittennotificationprovidedtoprovider|DateForwardedToIRE|Whomadetherequest|Issuedescriptionandtypeofservice|Wastheinitialorganizationdeterminationrequestdeniedforlackofmedicalnecessity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Typeofreconsiderationcase|ReviewEntity|Datetheoverturneddecisionwasreceived|Timetheoverturneddecisionwasreceived|PartBDrugRequest|Dateoverturneddecisionorpaymenteffectuatedinthesystem|Timeoverturneddecisionorpaymenteffectuatedinthesystem|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamandRelatedEntity|Datethegrievancewasreceived|Timethegrievancewasreceived|AOREquivalentnoticeReceiptDate|AOREquivalentnoticeReceiptTime|Howwasthegrievancereceived|WasthegrievanceprocessedasStandardorExpedited|Categoryoftheissue|GrievanceDescription|Wasthisprocessedasaqualityofcaregrievance|Wasatimeframeextensiontaken|Dateoralnotificationprovidedtoenrollee|Timeoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Whomadetherequest|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|DateDSNPAIPnotifiedenrolleeofitsdecisiontoreducesuspendorterminateservices|Effectivedateofreductionsuspensionorterminationofservices|Wasthedecisionappealed|Whomadetherequest|Datetheappealwasreceived|AOREquivalentnoticereceiptdate|WastheappealprocessedasStandardorExpedited|Wasappealmadeundertheexpeditedtimeframebutprocessedbytheplanunderthestandardtimeframe|Wasatimeframeextensiontaken|Didtheenrolleerequestcontinuationofbenefits|Werethebenefitsunderappealprovidedtotheenrolleeduringtheplanlevelappealprocess|RequestDisposition|DateofDSNPAIPdecision|Dateoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrolleeprovider|DatereconsidereddeterminationeffectuatedintheDSNPAIPsystem|DateforwardedtoIREifdeniedoruntimely|Ifrequestdenieddateserviceswereterminatedreducedsuspended|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DrugNameStrengthandDosageForm|NDC|Isthisaprotectedclassdrug|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|AOREquivalentnoticeReceiptDate|AOREquivalentnoticeReceiptTime|RequestDetermination|WastherequestprocessedasStandardorExpedited|Wastheoriginalrequestmadeunderthestandardtimeframeandlaterrequestedtobeexpedited|Daterequestwasupgradedtoexpedited|Timetherequestwasupgradedtoexpedited|IssueDescription|FormularyUMType|DateofDetermination|TimeofDetermination|Dateeffectuatedinthesystem|Timeeffectuatedinthesystem|Dateoralnotificationprovidedtoenrollee|Timeoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Whomadetherequest|DateforwardedtoIRE|TimeforwardedtoIRE|FirstTierDownstreamAndRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DrugNameStrengthandDosageForm|NDC|Isthisaprotectedclassdrug|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|AOREquivalentnoticeReceiptDate|AOREquivalentnoticeReceiptTime|RequestDetermination|WastherequestprocessedasStandardorExpedited|Wastheoriginalrequestmadeunderthestandardtimeframeandlaterrequestedtobeexpedited|Daterequestwasupgradedtoexpedited|Timerequestwasupgradedtoexpedited|IssueDescription|ExceptionType|UMExceptionType|Dateprescribersupportingstatementreceived|Timeprescribersupportingstatementreceived|DateofDetermination|TimeofDetermination|Dateeffectuatedinthesystem|Timeeffectuatedinthesystem|Expirationdateoftheapproval|Dateoralnotificationprovidedtoenrollee|Timeoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Whomadetherequest|DateforwardedtoIRE|TimeforwardedtoIRE|FirstTierDownstreamandRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DrugNameStrengthandDosageForm|NDC|Isthisaprotectedclassdrug|AuthorizationorClaimNumber|Datetherequestwasreceived|AOREquivalentnoticeReceiptDate|TypeofRequest|RequestDetermination|Wastherequestprocessedasanexceptionrequest|IssueDescription|ExceptionType|UMExceptionType|Dateprescribersupportingstatementreceived|Wasthecoveragedeterminationrequestdeniedforlackofmedicalnecessity|DateofDetermination|Dateeffectuatedinthesystem|Expirationdateoftheapproval|Datewrittennotificationprovidedtoenrollee|Whomadetherequest|Datereimbursementprovided|DateforwardedtoIRE|FirstTierDownstreamAndRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DrugNameStrengthandDosageForm|NDC|Isthisaprotectedclassdrug|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|AOREquivalentnoticeReceiptDate|AOREquivalentnoticeReceiptTime|Isthisanappealofanatriskdetermination|RequestDetermination|WastherequestprocessedasStandardorExpedited|Wastheoriginalrequestmadeunderthestandardtimeframeandlaterrequestedtobeexpedited|Daterequestwasupgradedtoexpedited|Timerequestwasupgradedtoexpedited|IssueDescription|ExceptionType|UMExceptionType|Wasthecoveragedeterminationrequestdeniedforlackofmedicalnecessity|DateofDetermination|TimeofDetermination|Dateeffectuatedinthesystem|Timeeffectuatedinthesystem|Expirationdateoftheapproval|Dateoralnotificationprovidedtoenrollee|Timeoralnotificationprovidedtoenrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Whomadetherequest|DateforwardedtoIRE|TimeforwardedtoIRE|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DrugNameStrengthandDosageForm|NDC|Isthisaprotectedclassdrug|AuthorizationorClaimNumber|Datetheoverturndecisionwasreceived|Timetheoverturndecisionwasreceived|TypeofRequestreversedbyreviewentity|Datetheoverturndecisionwaseffectuatedinthesystem|Timetheoverturndecisionwaseffectuatedinthesystem|Datereimbursementprovided|Expirationdateoftheapproval|FirstTierDownstreamAndRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DateGrievancewasReceived|TimeTheGrievancewasReceived|AOREquivalentnoticeReceiptDate|AOREquivalentnoticeReceiptTime|Howwasthegrievancereceived|WasthegrievanceprocessedasStandardorExpedited|Categoryoftheissue|GrievanceDescription|Wasthisprocessedasaqualityofcaregrievance|Wasatimeframeextensiontaken|DateOralNotificationProvidedToEnrollee|TimeOralNotificationProvidedToEnrollee|Datewrittennotificationprovidedtoenrollee|Timewrittennotificationprovidedtoenrollee|Whomadetherequest|FirstTierDownstreamAndRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '*', 'HEADER_NAMES', '*', 'EnrolleeFirstName|EnrolleeLastName|EnrolleeID|ContractID|PlanBenefitPackagePBP|DrugNameStrengthandDosageForm|DatetheInitialWrittenNotificationofpotentialatriskstatuswasprovidedtoenrollee|DateSecondWrittenNotificationofAtRiskDeterminationProvidedtoEnrollee|DatetheAtRiskDeterminationwasmade|RequestDetermination|TypeofAtRiskLimitation|ConfirmationofAgreementtoPlaceLimitationuponEnrollee|Ifanenrolleeeditwasuseddatetheeditwaseffectuatedinthesystem|Expirationdateoftheatriskrestrictionlockin|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA1', '*', 'HEADER_NAMES', '*', 'EnrolleeID|EnrolleeFirstName|EnrolleeLastName|DateofBirth|EnrollmentEffectiveDate|EffectiveDisenrollmentDate|CardholderID|ContractID|PlanID|NDC|DateofService|DateofRejection|ClaimQuantity|ClaimDaysSupply|PatientResidence|PharmacyServiceType|CompoundCode|RejectReasonCode1|PharmacyMessage1|RejectReasonCode2|PharmacyMessage2|RejectReasonCode3|PharmacyMessage3|RejectReasonCode4|PharmacyMessage4|RejectReasonCode5|PharmacyMessage5|RejectReasonCode6|PharmacyMessage6|RxCLAIMNumber|FirstTierDownstreamandRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA2', '*', 'HEADER_NAMES', '*', 'EnrolleeID|EnrolleeFirstName|EnrolleeLastName|DateofBirth|EnrollmentEffectiveDate|EffectiveDisenrollmentDate|CardholderID|ContractID|PlanBenefitPackagePBP|NDC|DateofService|DateofRejection|ClaimQuantity|ClaimDaysSupply|PatientResidence|PharmacyServiceType|CompoundCode|RejectReasonCode1|PharmacyMessage1|RejectReasonCode2|PharmacyMessage2|RejectReasonCode3|PharmacyMessage3|RejectReasonCode4|PharmacyMessage4|RejectReasonCode5|PharmacyMessage5|RejectReasonCode6|RejectReasonCode6|RxCLAIMNumber|FirstTierDownstreamandRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA3', '*', 'HEADER_NAMES', '*', 'EnrolleeID|EnrolleeFirstName|EnrolleeLastName|DateofBirth|CardholderID|ContractID|PlanBenefitPackagePBP|NDC|DateofService|ClaimQuantity|ClaimDaysSupply|CompoundCode|FirstTierDownstreamandRelatedEntity|UniqueKey', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '*', 'HEADER_NAMES', '*', 'EnrolleeID|EnrolleeFirstName|EnrolleeLastName|DateofBirth|EnrollmentEffectiveDate|EffectiveDisenrollmentDate|CardholderID|ContractID|PlanBenefitPackagePBP|FirstTierDownstreamandRelatedEntity|UniqueKey', 1, 1);

-- ----------------------------------------------------------------------------------------------------
-- 7a. File checks: the staging column of each field, in file order (TARGET_COLUMNS)
-- From the TargetColumnList of each table (without the row ID and the columns the framework fills). '*' = the
-- general layout (CMSDE2022), source 51 = the MedHOK layout (CMSDE2022_MHK), which has extra fields. CDAG1 - CDAG5 and
-- FA1 - FA4 have no list: their fields go to the staging columns of the same name when every header name is
-- one, otherwise to the staging table's data columns in table order (as FA2, whose header lists RejectReasonCode6 twice). Switched off for the internal sources (their SELECT column
-- names name the staging columns).
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AORReceiptDate|AORReceiptTime|RequestDetermination|WasTheRequestProcessedAsStdOrExpt|Wasatimeframeextensiontaken|DateOfDetermination|TimeOfDetermination|DateoralnotificationprovidedtoMember|TimeoralnotificationprovidedtoMember|DateWrittenNotificationprovidedToMember|TimeWrittenNotificationprovidedToMember|Whomadetherequest|Issuedescriptionandtypeofservice|WasAnExptRequestMadeButProcessedAsStd|WasTheRequestDeniedForLackOfMedicalNecessity|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AORReceiptDate|AORReceiptTime|RequestDetermination|WasTheRequestProcessedAsStdOrExpt|Wasatimeframeextensiontaken|DateOfDetermination|TimeOfDetermination|DateoralnotificationprovidedtoMember|TimeoralnotificationprovidedtoMember|DateWrittenNotificationprovidedToMember|TimeWrittenNotificationprovidedToMember|DateReconsideredDeterminationEffectuatedInTheSystem|TimeReconsideredDeterminationEffectuatedInTheSystem|DateForwardedToIRE|TimeForwardedToIRE|Whomadetherequest|Issuedescriptionandtypeofservice|WasAnExptRequestMadeButProcessedAsStd|WasTheInitOrgDetReqtDeniedForLackOfMediNecessity|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|AORReceiptDate|WaiverofLiabilityReceiptDate|WasItaCleanClaim|WasTheRequestProcessedasanODorRecon|RequestDetermination|DateofDetermination|DateClaimReconsiderationwasPaid|DateWrittenNotificationprovidedToMember|DateWrittenNotificationProvidedtoProvider|DateForwardedtoIRE|Whomadetherequest|Issuedescriptionandtypeofservice|WasTheInitOrgDetReqtDeniedForLackOfMediNecessity|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|TypeofReconsiderationCase|ReviewEntity|DateTheOverturnedDecisionWasReceived|TimeTheOverturnedDecisionWasReceived|PartBDrugRequest|DateOverturnedDecisionOrPaymentEffectuatedInTheSystem|TimeOverturnedDecisionOrPaymentEffectuatedInTheSystem|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|DateTheGrievanceWasReceived|TimeTheGrievanceWasReceived|AORReceiptDate|AORReceiptTime|HowWasTheGrievanceReceived|WasTheGrievanceProcessedAsStdOrExpt|CategoryOfTheIssue|GrievanceDescription|WasThisProcesseAsAQualityOfCareGrievance|Wasatimeframeextensiontaken|DateOralNotificationProvidedToMember|TimeOralNotificationProvidedToMember|DateWrittenNotificationProvidedToMember|TimeWrittenNotificationProvidedToMember|Whomadetherequest|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|DateOfDSNPAIP|EffectiveDateOfRedSusOrTermOfServices|WasTheDecisionAppealed|WhoMadeTheRequest|DateTheAppealWasReceived|AORReceiptDate|WasTheAppealProcessedAsStdOrExpt|WasAppealMadeUnderTheExpTFButProByPlanUnderStandTF|WasaTimeframeExtensionTaken|DidTheMemberRequestContinuationOfBenefits|WereTheBenefitsUnderAppProvidedDuringPlanLvlAppProcess|RequestDisposition|DateOfDSNPAIPDecision|DateOralNotificationProvidedToMember|DateWrittenNotificationProvidedToMemberProvider|DateReconsideredDeteEffInDSNPAIPSystem|DateForwardedToIREIfdeniedOrUntimely|ReqDeniedDateservicesTerminatedReducedSuspended|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|DateTheGrievanceWasReceived|TimeTheGrievanceWasReceived|AORReceiptDate|AORReceiptTime|HowWasTheGrievanceReceived|WasTheGrievanceProcessedAsStdOrExpt|CategoryOfTheIssue|GrievanceDescription|WasThisProcessedAsAQualityOfCareGrievance|WasATimeframeExtensionTaken|DateOralNotificationProvidedToMember|TimeOralNotificationProvidedToMember|DateWrittenNotificationProvidedToMember|TimeWrittenNotificationProvidedToMember|WhoMadeTheRequest|FirstTierDownstreamandRelatedEntity|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '*', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|DrugNameStrengthAndDosageForm|DateTheInitialWrittenNotificationOfPotentialAtRiskStatusWasProvidedToMember|DateSecondWrittenNotificationOfAtRiskDeterminationProvidedToMember|DateTheAtRiskDeterminationWasMade|RequestDetermination|TypeOfAtRiskLimitation|ConfirmationOfAgreementToPlaceLimitationUponMember|IfAnMemberEditWasUsedDateTheEditWasEffectuatedInTheSystem|ExpirationDateOfTheAtRiskRestrictionLockIn|FirstTierDownstreamandRelatedEntity|SourceUnique_ID', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AORReceiptDate|AORReceiptTime|RequestDetermination|WasTheRequestProcessedAsStdOrExpt|Wasatimeframeextensiontaken|DateOfDetermination|TimeOfDetermination|DateoralnotificationprovidedtoMember|TimeoralnotificationprovidedtoMember|DateWrittenNotificationprovidedToMember|TimeWrittenNotificationprovidedToMember|Whomadetherequest|Issuedescriptionandtypeofservice|WasAnExptRequestMadeButProcessedAsStd|WasTheRequestDeniedForLackOfMedicalNecessity|SourceUnique_ID|Revision|Filler1|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|Timetherequestwasreceived|PartBDrugRequest|AORReceiptDate|AORReceiptTime|RequestDetermination|WasTheRequestProcessedAsStdOrExpt|Wasatimeframeextensiontaken|DateOfDetermination|TimeOfDetermination|DateoralnotificationprovidedtoMember|TimeoralnotificationprovidedtoMember|DateWrittenNotificationprovidedToMember|TimeWrittenNotificationprovidedToMember|DateReconsideredDeterminationEffectuatedInTheSystem|TimeReconsideredDeterminationEffectuatedInTheSystem|DateForwardedToIRE|TimeForwardedToIRE|Whomadetherequest|Issuedescriptionandtypeofservice|WasAnExptRequestMadeButProcessedAsStd|WasTheInitOrgDetReqtDeniedForLackOfMediNecessity|ExRMNumber|SourceUnique_ID|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|Datetherequestwasreceived|AORReceiptDate|WaiverofLiabilityReceiptDate|WasItaCleanClaim|WasTheRequestProcessedasanODorRecon|RequestDetermination|DateofDetermination|DateClaimReconsiderationwasPaid|DateWrittenNotificationprovidedToMember|DateWrittenNotificationProvidedtoProvider|DateForwardedtoIRE|Whomadetherequest|Issuedescriptionandtypeofservice|WasTheInitOrgDetReqtDeniedForLackOfMediNecessity|ExRMNumber|SourceUnique_ID|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|TypeofReconsiderationCase|ReviewEntity|DateTheOverturnedDecisionWasReceived|TimeTheOverturnedDecisionWasReceived|PartBDrugRequest|DateOverturnedDecisionOrPaymentEffectuatedInTheSystem|TimeOverturnedDecisionOrPaymentEffectuatedInTheSystem|AppealNumber|SourceUnique_ID|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|DateTheGrievanceWasReceived|TimeTheGrievanceWasReceived|AORReceiptDate|AORReceiptTime|HowWasTheGrievanceReceived|WasTheGrievanceProcessedAsStdOrExpt|CategoryOfTheIssue|GrievanceDescription|WasThisProcesseAsAQualityOfCareGrievance|Wasatimeframeextensiontaken|DateOralNotificationProvidedToMember|TimeOralNotificationProvidedToMember|DateWrittenNotificationProvidedToMember|TimeWrittenNotificationProvidedToMember|Whomadetherequest|GrievanceNumber|SourceUnique_ID|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|FirstTierDownstreamandRelatedEntity|AuthorizationorClaimNumber|DateOfDSNPAIP|EffectiveDateOfRedSusOrTermOfServices|WasTheDecisionAppealed|WhoMadeTheRequest|DateTheAppealWasReceived|AORReceiptDate|WasTheAppealProcessedAsStdOrExpt|WasAppealMadeUnderTheExpTFButProByPlanUnderStandTF|WasaTimeframeExtensionTaken|DidTheMemberRequestContinuationOfBenefits|WereTheBenefitsUnderAppProvidedDuringPlanLvlAppProcess|RequestDisposition|DateOfDSNPAIPDecision|DateOralNotificationProvidedToMember|DateWrittenNotificationProvidedToMemberProvider|DateReconsideredDeteEffInDSNPAIPSystem|DateForwardedToIREIfdeniedOrUntimely|ReqDeniedDateservicesTerminatedReducedSuspended|SourceUnique_ID|RevisionNumber|AppealNo', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|DateTheGrievanceWasReceived|TimeTheGrievanceWasReceived|AORReceiptDate|AORReceiptTime|HowWasTheGrievanceReceived|WasTheGrievanceProcessedAsStdOrExpt|CategoryOfTheIssue|GrievanceDescription|WasThisProcessedAsAQualityOfCareGrievance|WasATimeframeExtensionTaken|DateOralNotificationProvidedToMember|TimeOralNotificationProvidedToMember|DateWrittenNotificationProvidedToMember|TimeWrittenNotificationProvidedToMember|WhoMadeTheRequest|SourceUnique_ID|Filler1|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '51', 'TARGET_COLUMNS', '*', 'MemberFirstName|MemberLastName|MemberID|ContractID|PlanBenefitPackage|DrugNameStrengthAndDosageForm|DateTheInitialWrittenNotificationOfPotentialAtRiskStatusWasProvidedToMember|DateSecondWrittenNotificationOfAtRiskDeterminationProvidedToMember|DateTheAtRiskDeterminationWasMade|RequestDetermination|TypeOfAtRiskLimitation|ConfirmationOfAgreementToPlaceLimitationUponMember|IfAnMemberEditWasUsedDateTheEditWasEffectuatedInTheSystem|ExpirationDateOfTheAtRiskRestrictionLockIn|SourceUnique_ID|Filler1|Filler2|Filler3', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '10', 'TARGET_COLUMNS', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '40', 'TARGET_COLUMNS', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '210', 'TARGET_COLUMNS', '*', NULL, 0, 1);

-- ----------------------------------------------------------------------------------------------------
-- 5. File sources: one row per FDR source and table
-- File name CMSAUTH_<source>_<file type>_22_<run type>_<report start YYYYMMDD>_<report end YYYYMMDD>_<YYYYMMDDHHMISS>.txt
-- (case is ignored). <source> = the source name (MHK for MedHOK). Pipe delimited, header and trailer lines.
-- Inbound folder: share chs_dms on the server of the NAS Airflow Connection of the environment (wdc_comp_oper_nas_var),
-- folder Dev/OSS_Inbound/External/CMSUNIV, the same in every environment. Archive / Error / ErrorLog / ControlReport
-- are created in it. Staging CMSUNIV_FILELAND_$ENV_T, core CMSUNIV_CORE_$ENV_T with the same table name.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '101', 'CMSAUTH_ADVOCATE_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '103', 'CMSAUTH_ASH_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '110', 'CMSAUTH_Evicore_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '120', 'CMSAUTH_WellMed_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '124', 'CMSAUTH_EviCore Medical Oncology_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '134', 'CMSAUTH_Evicore-RadiationTherapy_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '203', 'CMSAUTH_EviCore MSK_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '218', 'CMSAUTH_EvicoreDME_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '219', 'CMSAUTH_EvicorePAC_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '51', 'CMSAUTH_MHK_OD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '51', 'CMSAUTH_MHK_RECON_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE2_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE2_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '101', 'CMSAUTH_ADVOCATE_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '103', 'CMSAUTH_ASH_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '106', 'CMSAUTH_CBH_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '107', 'CMSAUTH_CDH_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '120', 'CMSAUTH_WellMed_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '121', 'CMSAUTH_HCS_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '154', 'CMSAUTH_CDHAllow_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '175', 'CMSAUTH_ASH Acupuncture (ASHAP)_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '213', 'CMSAUTH_EYEMED_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '217', 'CMSAUTH_CDHDHMO_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'CMSAUTH_MHK_PYMT_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '51', 'CMSAUTH_MHK_EFF_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE4_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '51', 'CMSAUTH_MHK_GRV_C_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE5_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE5_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '51', 'CMSAUTH_MHK_AIP_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'ODAG_DE_TABLE6_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_DE_TABLE6_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', '64', 'CMSAUTH_ESI_CD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', '64', 'CMSAUTH_ESI_CDER_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE2_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE2_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', '64', 'CMSAUTH_ESI_PYMT_D_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', '64', 'CMSAUTH_ESI_RD_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE4_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', '64', 'CMSAUTH_ESI_EFF_D_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE5_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE5_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '32', 'CMSAUTH_Convey_GRV_D_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE6_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE6_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '51', 'CMSAUTH_MHK_GRV_D_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE6_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE6_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '64', 'CMSAUTH_ESI_GRV_D_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE6_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE6_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '51', 'CMSAUTH_MHK_AR_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'CDAG_DE_TABLE7_2022', 'CMSUNIV_CORE_$ENV_T', 'CDAG_DE_TABLE7_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA1', '64', 'CMSAUTH_ESI_RCFA_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'FA_DE_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_DE_TABLE1_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA2', '64', 'CMSAUTH_ESI_RCT_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'FA_DE_TABLE2_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_DE_TABLE2_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA3', '64', 'CMSAUTH_ESI_PDE_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'FA_DE_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_DE_TABLE3_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '32', 'CMSAUTH_Convey_NE_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'FA_DE_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_DE_TABLE4_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '64', 'CMSAUTH_ESI_NE_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'FA_DE_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_DE_TABLE4_2022', '22', 1);
INSERT INTO HSABC_DEV.ComplianceSourceFileConfig (Project_Cd, Table_Nm, Src_ID, Src_File_Nm_Tmplt, Delmtr_Cd, Src_File_Has_Hdr_Ind, Src_File_Has_Trlr_Ind, Allow_Zero_Rcd_Ind, Src_File_Path, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, File_Vrsn_Cd, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '901', 'CMSAUTH_Convey-Miramar_NE_{VERSION}_{RUNTY}_{RPTSTART}_{RPTEND}_{TS}.txt', '|', 1, 1, 0, 'nas://chs_dms/Dev/OSS_Inbound/External/CMSUNIV', 'CMSUNIV_FILELAND_$ENV_T', 'FA_DE_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_DE_TABLE4_2022', '22', 1);

-- ----------------------------------------------------------------------------------------------------
-- 7b. File checks: record type
-- General files start every line with a record type: H (header), D (data), T (trailer). MedHOK (51) files have
-- no record type field, so the check is switched off for 51.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', '*', '*', 'RECORD_TYPE', '*', 'H,D,T', 1, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '51', 'RECORD_TYPE', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '51', 'RECORD_TYPE', '*', NULL, 0, 1);

-- ----------------------------------------------------------------------------------------------------
-- 7d. File checks: trailer
-- The last line: T, the file name, the number of lines including the header and the trailer, a timestamp
-- (YYYYMMDD, YYYYMMDDHHMM or YYYYMMDDHHMMSS).
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', '*', '*', 'TRAILER', '*', 'RECORD_TYPE,FILE_NAME,TOTAL_ROW_COUNT,TIMESTAMP', 1, 1);

-- ----------------------------------------------------------------------------------------------------
-- 7c. File checks: MedHOK header
-- MedHOK (51) files have their own fields (TARGET_COLUMNS for 51), so the general header names do not apply to them.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', '51', 'HEADER_NAMES', '*', NULL, 0, 1);
INSERT INTO HSABC_DEV.ComplianceFileCheckConfig (Project_Cd, Table_Nm, Src_ID, Check_Cd, Check_Field_Nm, Check_Val_Txt, Include_Ind, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', '51', 'HEADER_NAMES', '*', NULL, 0, 1);

-- ----------------------------------------------------------------------------------------------------
-- 6. Columns the framework fills (staging and core)
-- Every other staging column is a file field (TARGET_COLUMNS) or a column of the internal source's SELECT.
-- Btch_ID is always the batch ID (no row), Batch_ID (internal sources' staging and core tables) is mapped to it. AuditDataID = the load ID the framework gives every file / pull (the ID of
-- the ComplianceFileLoad row, used in its audit reporting). Source_ID / Universe_Version are the names in the internal
-- sources' staging tables. StagingCreateDate = the load date (Central), set in staging and copied unchanged to core
-- and the export. <table>_ID = a running row number in the file staging and core tables.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Src_Data_Key', 'SOURCE_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Source_ID', 'SOURCE_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Universe_Vrsn', 'VERSION', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Universe_Version', 'VERSION', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Run_Ty', 'RUN_TYPE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'FileName', 'FILE_NAME', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Load_Dtts', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'LastUpdt_Dtts', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'StagingCreateDate', 'LOAD_DATE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'AuditDataID', 'LOAD_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'Batch_ID', 'BATCH_ID', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', 'ODAG_DE_Table1_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', 'ODAG_DE_Table2_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'ODAG_DE_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', 'ODAG_DE_Table4_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', 'ODAG_DE_Table5_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', 'ODAG_DE_Table6_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', 'CDAG_DE_Table1_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', 'CDAG_DE_Table2_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', 'CDAG_DE_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', 'CDAG_DE_Table4_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', 'CDAG_DE_Table5_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', 'CDAG_DE_Table6_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', 'CDAG_DE_Table7_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'FA_DE_Table1_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'FA_DE_Table2_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'FA_DE_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'FA_DE_Table4_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'ODAG_CSF_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'FA_CSF_Table4_2022_ID', 'ROW_SEQUENCE', NULL, 1);
-- Internal sources: the ID the query returns is kept; without it the framework numbers the rows (ROW_SEQUENCE).
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'ODAG_HS_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'ODAG_CignaAZ_Table3_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'FA_HS_Table4_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'FA_CignaAZ_TABLE4_2022_ID', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', 'SNPCC_HS_Table1_2022_ID', 'ROW_SEQUENCE', NULL, 1);
-- Export feed (EXPORT_FEED_$ENV_T.SDO_CMSUNIV_<table>_2022): its framework columns, and its key = the core row ID.
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'ETL_LOAD_DT_TM', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'ETL_LAST_UPDT_DT_TM', 'LOAD_TIMESTAMP', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'STG_CRET_DTM', 'LOAD_DATE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'UNVRS_VRSN_CD', 'VERSION', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', '*', 'FILE_NM', 'FILE_NAME', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', 'SDO_CMSUNIV_ODAG1_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', 'SDO_CMSUNIV_ODAG2_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'SDO_CMSUNIV_ODAG3_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', 'SDO_CMSUNIV_ODAG4_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', 'SDO_CMSUNIV_ODAG5_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', 'SDO_CMSUNIV_ODAG6_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', 'SDO_CMSUNIV_CDAG1_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', 'SDO_CMSUNIV_CDAG2_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', 'SDO_CMSUNIV_CDAG3_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', 'SDO_CMSUNIV_CDAG4_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', 'SDO_CMSUNIV_CDAG5_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', 'SDO_CMSUNIV_CDAG6_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', 'SDO_CMSUNIV_CDAG7_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'SDO_CMSUNIV_FA1_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'SDO_CMSUNIV_FA2_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'SDO_CMSUNIV_FA3_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'SDO_CMSUNIV_FA4_2022_KEY', 'ROW_SEQUENCE', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceTargetColumnConfig (Project_Cd, Table_Nm, Column_Nm, Value_Cd, Value_Txt, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', 'SDO_CMSUNIV_SNPCC1_2022_KEY', 'ROW_SEQUENCE', NULL, 1);

-- ----------------------------------------------------------------------------------------------------
-- 5a. Internal sources pulled from SQL Server (SOURCE_LOAD)
-- Pulled by batch: the source tables carry the framework's batch ID in Batch_ID (QNXT queries also by Source_ID). Connections SQL_CHS (QNXT, 10),
-- SQL_AZ (AZ, 40), SQL_SNPCC (DatamartSNPMOC, 11). The SELECT returns the data columns only, the framework fills
-- Btch_ID, Source_ID, Universe_Version, Run_Ty, Load_Dtts, StagingCreateDate and AuditDataID. Staging
-- CMSUNIV_STAGE_$ENV_T, core CMSUNIV_CORE_$ENV_T with the same table name. Each returned column is named after its
-- staging column (AS); the ID comes from the source. Staging -> core: same names by name, the others by column order
-- (SNPCC1: staging Member... -> core Enrollee...). Facets (210): Teradata, on the framework's own connection (no
-- Src_Conn_Cd); the ID is numbered by the framework (ROW_SEQUENCE, above the highest in staging and core).
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '10', 'SQLSERVER', 'SQL_CHS', 'SELECT ODAG_Table3_ID AS ODAG_HS_Table3_2022_ID,EnrolleeFirstName AS MemberFirstName,EnrolleeLastName AS MemberLastName,EnrolleeID AS MemberID,ContractID,PlanBenefitPackage,FirstTierDownstreamAndRelatedEntity,AuthorizationOrClaimNumber,DateTheRequestWasReceived,AORReceiptDate,WaiverOfLiabilityReceiptDate,WasItACleanClaim,WasTheRequestProcessedAsAnODOrRECON,RequestDetermination,DateOfDetermination,DateClaimReconsiderationWasPaid,DateWrittenNotificationProvidedToEnrollee AS DateWrittenNotificationprovidedToMember,DateWrittenNotificationProvidedToProvider,DateForwardedToIRE,WhoMadeTheRequest,IssueDescriptionAndTypeOfService,WasTheInitialOrganizationDeterminationRequestDeniedForLackOfMedicalNecessity AS WasTheInitOrgDetReqtDeniedForLackOfMediNecessity,SourceUnique_ID,IDN_Flag FROM Staging.cms2022.ODAG_Table3_2022 WHERE Batch_ID = {btch_id} AND CAST(Source_ID AS VARCHAR(5)) = {src_id}', 'CMSUNIV_STAGE_$ENV_T', 'ODAG_HS_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_HS_TABLE3_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '10', 'SQLSERVER', 'SQL_CHS', 'SELECT FA_Table4_ID AS FA_HS_Table4_2022_ID,EnrolleeID AS MemberID,EnrolleeFirstName AS MemberFirstName,EnrolleeLastName AS MemberLastName,DateofBirth,EnrollmentEffectiveDate,EffectiveDisenrollmentDate,CardholderID,ContractID,PlanBenefitPackage,SourceUnique_ID FROM Staging.cms2022.FA_Table4_2022 WHERE Batch_ID = {btch_id} AND CAST(Source_ID AS VARCHAR(5)) = {src_id}', 'CMSUNIV_STAGE_$ENV_T', 'FA_HS_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_HS_TABLE4_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '40', 'SQLSERVER', 'SQL_AZ', 'SELECT ODAG_CignaAZ_Table3_2022_ID,MemberFirstName,MemberLastName,MemberID,ContractID,PlanBenefitPackage,FirstTierDownstreamandRelatedEntity,AuthorizationorClaimNumber,Datetherequestwasreceived,AORReceiptDate,WaiverofLiabilityReceiptDate,WasItaCleanClaim,WasTheRequestProcessedasanODorRecon,RequestDetermination,DateofDetermination,DateClaimReconsiderationwasPaid,DateWrittenNotificationprovidedToMember,DateWrittenNotificationProvidedtoProvider,DateForwardedtoIRE,Whomadetherequest,Issuedescriptionandtypeofservice,WasTheInitOrgDetReqtDeniedForLackOfMediNecessity,SourceUnique_ID FROM AZCMS.dbo.ODAG_CignaAZ_Table3_2022 WHERE Batch_ID = {btch_id}', 'CMSUNIV_STAGE_$ENV_T', 'ODAG_CIGNAAZ_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_CIGNAAZ_TABLE3_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '40', 'SQLSERVER', 'SQL_AZ', 'SELECT FA_CignaAZ_TABLE4_2022_ID,EnrolleeID,EnrolleeFirstName,EnrolleeLastName,DateofBirth,EnrollmentEffectiveDate,EffectiveDisenrollmentDate,CardholderID,ContractID,PlanBenefitPackage,SourceUnique_ID FROM AZCMS.dbo.FA_CignaAZ_TABLE4_2022 WHERE Batch_ID = {btch_id}', 'CMSUNIV_STAGE_$ENV_T', 'FA_CIGNAAZ_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_CIGNAAZ_TABLE4_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', '210', 'TERADATA', NULL, 'SELECT A_FirstName AS MemberFirstName, B_LastName AS MemberLastName, C_EnrolleeID AS MemberID, D_ContractID AS ContractID, E_Planid AS PlanBenefitPackage, F_FDR AS FirstTierDownstreamandRelatedEntity, G_Claimid AS AuthorizationorClaimNumber, H_DateReceived AS Datetherequestwasreceived, I_DateAORReceipt AS AORReceiptDate, J_DateWOL AS WaiverofLiabilityReceiptDate, K_CleanClaim AS WasItaCleanClaim, L_ODorRecon AS WasTheRequestProcessedasanODorRecon, M_RequestDetermination AS RequestDetermination, N_DateDetermination AS DateofDetermination, O_DatePaid AS DateClaimReconsiderationwasPaid, P_DateNotifyMember AS DateWrittenNotificationprovidedToMember, Q_DateNotifyProvider AS DateWrittenNotificationProvidedtoProvider, R_DateIRE AS DateForwardedtoIRE, S_WhoMadeRequest AS Whomadetherequest, T_IssueDescTypeOfService AS Issuedescriptionandtypeofservice, U_DeniedMedicalNecessity AS WasTheInitOrgDetReqtDeniedForLackOfMediNecessity, SourceUnique_ID, IDN_Flag FROM CMSUNIV_CORE_DEV_V.CSF_ODAG_Table3_2022 WHERE Batch_ID = {btch_id}', 'CMSUNIV_STAGE_$ENV_T', 'ODAG_CSF_TABLE3_2022', 'CMSUNIV_CORE_$ENV_T', 'ODAG_CSF_TABLE3_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA4', '210', 'TERADATA', NULL, 'SELECT A_EnrolleeID AS MemberID, B_FirstName AS MemberFirstName, C_LastName AS MemberLastName, D_DOB AS DateofBirth, E_EffDate AS EnrollmentEffectiveDate, F_TermDate AS EffectiveDisenrollmentDate, G_Cardholder AS CardholderID, H_ContractID AS ContractID, I_PBP AS PlanBenefitPackage, '''' AS FirstTierDownstreamAndRelatedEntity, SourceUnique_ID FROM CMSUNIV_CORE_DEV_V.CSF_FA_Table4_2022 WHERE Batch_ID = {btch_id}', 'CMSUNIV_STAGE_$ENV_T', 'FA_CSF_TABLE4_2022', 'CMSUNIV_CORE_$ENV_T', 'FA_CSF_TABLE4_2022', 1);
INSERT INTO HSABC_DEV.ComplianceInternalSourceConfig (Project_Cd, Table_Nm, Src_ID, Src_Db_Ty, Src_Conn_Cd, Src_Sql_Txt, Stg_Schema_Nm, Stg_Table_Nm, Core_Schema_Nm, Core_Table_Nm, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', '11', 'SQLSERVER', 'SQL_SNPCC', 'SELECT SNPCC_Table1_ID AS SNPCC_HS_Table1_2022_ID, EnrolleeFirstName AS MemberFirstName, EnrolleeLastName AS MemberLastName, EnrolleeID AS MemberID, ContractID, PlanBenefitPackage, PlanType, EnrollmentEffectiveDate, MostRecentPlanChangeEffectiveDate, DateOfMostRecentHRA, DateOfPreviousHRA, DateInitialHRAWasCompleted, EnrolleeRiskStratificationAuditLetter AS MemberRiskStratificationAuditLetter, DateMostRecentIndividualizedCarePlan, WasICTCreatedIdentified, SourceUnique_ID FROM QUALITY.SNPMOC.Table1_cms2022 WHERE Batch_ID = {btch_id}', 'CMSUNIV_STAGE_$ENV_T', 'SNPCC_HS_TABLE1_2022', 'CMSUNIV_CORE_$ENV_T', 'SNPCC_HS_TABLE1_2022', 1);

-- ----------------------------------------------------------------------------------------------------
-- 8. Export feed: the existing feed tables EXPORT_FEED_$ENV_T.SDO_CMSUNIV_<table>_2022, exported by the framework (no
-- procedure, no PROVISIONING_WORK: rows come straight from core; run types are chosen by the EXPORT_LOAD DAG's scope)
-- For each batch and source that is due (or released), EXPORT_LOAD removes the source's earlier export rows of the
-- period and copies its rows from the source's own core table: columns of the same name, the framework's columns by
-- what fills them (batch, source, version, ...), the rest by column order (ODAG3 / FA4 sources 10 and 40 come from
-- their own core tables with other names); the feed's batch column (Btch_ID) = the batch. validate-config lists the pairs and the export
-- columns a core table cannot fill.
-- ----------------------------------------------------------------------------------------------------
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG1', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG1_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG2', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG2_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG3', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG3_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG4', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG4_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG5', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG5_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'ODAG6', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_ODAG6_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG1', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG1_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG2', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG2_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG3', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG3_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG4', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG4_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG5', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG5_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG6', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG6_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'CDAG7', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_CDAG7_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA1', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_FA1_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA2', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_FA2_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA3', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_FA3_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'FA4', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_FA4_2022', NULL, 1);
INSERT INTO HSABC_DEV.ComplianceExportConfig (Project_Cd, Table_Nm, Exp_Schema_Nm, Exp_Table_Nm, Exp_Btch_Col_Nm, Active_Ind)
VALUES ('CMSUNIV', 'SNPCC1', 'EXPORT_FEED_$ENV_T', 'SDO_CMSUNIV_SNPCC1_2022', NULL, 1);

-- ----------------------------------------------------------------------------------------------------
-- 9. Ad-hoc requests (CMS): one row per source, when a request comes in (example, commented out)
-- Each request names its source and compliance version, no crosswalk row is needed. Req_Start / Req_End = the
-- days BATCH_CREATION makes the batch (the same day twice = one batch).
-- ----------------------------------------------------------------------------------------------------
-- INSERT INTO HSABC_DEV.ComplianceRequestInTake (Project_Cd, Table_Nm, Src_ID, Run_Ty, Cmplnc_Vrsn, Rpt_Start_Dt_Key,
--   Rpt_End_Dt_Key, Req_Start_Dt_Key, Req_End_Dt_Key)
-- VALUES ('CMSUNIV', 'ODAG3', '51', 'CMS', '2200', DATE '2026-07-01', DATE '2026-09-30', DATE '2026-10-20', DATE '2026-10-20');
