-- CMSUNIV export feed (DEV names: replace _DEV_ for other environments). Run by the DBA, then the
-- ComplianceExportConfig rows in cmsuniv_setup.sql (section 8) point EXPORT_LOAD at these procedures.
--
-- Assumed: one export table per table in CMSUNIV_EXPORT_DEV_T, named <table>_EXPORT, with the columns of the table's core
-- table plus Exp_Btch_ID. For tables with FDR files that is the file core table (CMSUNIV_CORE_$ENV_T.<x>_DE_TABLE<n>_2022,
-- which holds every FDR source, MedHOK included); SNPCC1 has only its internal source, so its core table.
--
-- Each procedure exports one batch of one source, as EXPORT_LOAD calls it:
--   p_btch_id       the batch being exported
--   p_data_btch_id  the batch whose core rows are its data (itself, the batch it carries forward, NULL = no data)
--   p_src_id, p_run_ty, p_rpt_start, p_rpt_end  the source and the batch's run type and report dates
-- It removes the source's export rows of every batch of the same run type and report dates (the latest batch
-- replaces earlier ones), copies the data batch's core rows, and stamps them with p_btch_id (Btch_ID and Exp_Btch_ID),
-- so a carried-forward batch appears under its own batch ID. EXPORT_LOAD then counts the batch's rows in the table.


-- ====================================================================================================
-- ODAG1
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG1_EXPORT AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE1_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG1_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_ODAG1 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.ODAG1_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG1' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG1_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE1_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.ODAG1_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- ODAG2
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG2_EXPORT AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE2_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG2_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_ODAG2 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.ODAG2_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG2' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG2_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE2_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.ODAG2_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- ODAG3
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE3_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_ODAG3 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG3' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    IF :p_src_id = '10' THEN   -- ODAG_HS_TABLE3_2022: its columns by position
      INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT
              (ODAG_DE_Table3_2022_ID,
               MemberFirstName,
               MemberLastName,
               MemberID,
               ContractID,
               PlanBenefitPackage,
               FirstTierDownstreamandRelatedEntity,
               AuthorizationorClaimNumber,
               Datetherequestwasreceived,
               AORReceiptDate,
               WaiverofLiabilityReceiptDate,
               WasItaCleanClaim,
               WasTheRequestProcessedasanODorRecon,
               RequestDetermination,
               DateofDetermination,
               DateClaimReconsiderationwasPaid,
               DateWrittenNotificationprovidedToMember,
               DateWrittenNotificationProvidedtoProvider,
               DateForwardedtoIRE,
               Whomadetherequest,
               Issuedescriptionandtypeofservice,
               WasTheInitOrgDetReqtDeniedForLackOfMediNecessity,
               SourceUnique_ID,
               StagingCreateDate,
               Universe_Vrsn,
               Run_Ty,
               AuditDataID,
               Src_Data_Key,
               Load_Dtts,
               Btch_ID)
        SELECT ODAG_Table3_ID,
               EnrolleeFirstName,
               EnrolleeLastName,
               EnrolleeID,
               ContractID,
               PlanBenefitPackage,
               FirstTierDownstreamAndRelatedEntity,
               AuthorizationOrClaimNumber,
               DateTheRequestWasReceived,
               AORReceiptDate,
               WaiverOfLiabilityReceiptDate,
               WasItACleanClaim,
               WasTheRequestProcessedAsAnODOrRECON,
               RequestDetermination,
               DateOfDetermination,
               DateClaimReconsiderationWasPaid,
               DateWrittenNotificationProvidedToEnrollee,
               DateWrittenNotificationProvidedToProvider,
               DateForwardedToIRE,
               WhoMadeTheRequest,
               IssueDescriptionAndTypeOfService,
               WasTheInitialOrganizationDeterminationRequestDeniedForLackOfMedicalNecessity,
               SourceUnique_ID,
               StagingCreateDate,
               Universe_Version,
               Run_Ty,
               AuditDataID,
               Source_ID,
               Load_Dtts,
               Batch_ID
          FROM CMSUNIV_CORE_DEV_T.ODAG_HS_TABLE3_2022
         WHERE Batch_ID = :p_data_btch_id AND CAST(Source_ID AS VARCHAR(30)) = :p_src_id;
    ELSEIF :p_src_id = '40' THEN   -- ODAG_CIGNAAZ_TABLE3_2022: its columns by position
      INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT
              (ODAG_DE_Table3_2022_ID,
               MemberFirstName,
               MemberLastName,
               MemberID,
               ContractID,
               PlanBenefitPackage,
               FirstTierDownstreamandRelatedEntity,
               AuthorizationorClaimNumber,
               Datetherequestwasreceived,
               AORReceiptDate,
               WaiverofLiabilityReceiptDate,
               WasItaCleanClaim,
               WasTheRequestProcessedasanODorRecon,
               RequestDetermination,
               DateofDetermination,
               DateClaimReconsiderationwasPaid,
               DateWrittenNotificationprovidedToMember,
               DateWrittenNotificationProvidedtoProvider,
               DateForwardedtoIRE,
               Whomadetherequest,
               Issuedescriptionandtypeofservice,
               WasTheInitOrgDetReqtDeniedForLackOfMediNecessity,
               SourceUnique_ID,
               StagingCreateDate,
               Universe_Vrsn,
               Run_Ty,
               AuditDataID,
               Src_Data_Key,
               Load_Dtts,
               Btch_ID)
        SELECT ODAG_CignaAZ_Table3_2022_ID,
               MemberFirstName,
               MemberLastName,
               MemberID,
               ContractID,
               PlanBenefitPackage,
               FirstTierDownstreamandRelatedEntity,
               AuthorizationorClaimNumber,
               Datetherequestwasreceived,
               AORReceiptDate,
               WaiverofLiabilityReceiptDate,
               WasItaCleanClaim,
               WasTheRequestProcessedasanODorRecon,
               RequestDetermination,
               DateofDetermination,
               DateClaimReconsiderationwasPaid,
               DateWrittenNotificationprovidedToMember,
               DateWrittenNotificationProvidedtoProvider,
               DateForwardedtoIRE,
               Whomadetherequest,
               Issuedescriptionandtypeofservice,
               WasTheInitOrgDetReqtDeniedForLackOfMediNecessity,
               SourceUnique_ID,
               StagingCreateDate,
               Universe_Version,
               Run_Ty,
               AuditDataID,
               Source_ID,
               Load_Dtts,
               Batch_ID
          FROM CMSUNIV_CORE_DEV_T.ODAG_CIGNAAZ_TABLE3_2022
         WHERE Batch_ID = :p_data_btch_id AND CAST(Source_ID AS VARCHAR(30)) = :p_src_id;
    ELSE
      INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE3_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    END IF;
    UPDATE CMSUNIV_EXPORT_DEV_T.ODAG3_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- ODAG4
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG4_EXPORT AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE4_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG4_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_ODAG4 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.ODAG4_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG4' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG4_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE4_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.ODAG4_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- ODAG5
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG5_EXPORT AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE5_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG5_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_ODAG5 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.ODAG5_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG5' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG5_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE5_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.ODAG5_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- ODAG6
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.ODAG6_EXPORT AS CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE6_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.ODAG6_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_ODAG6 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.ODAG6_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'ODAG6' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.ODAG6_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.ODAG_DE_TABLE6_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.ODAG6_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG1
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG1_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE1_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG1_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG1 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG1_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG1' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG1_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE1_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG1_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG2
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG2_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE2_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG2_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG2 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG2_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG2' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG2_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE2_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG2_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG3
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG3_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE3_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG3_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG3 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG3_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG3' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG3_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE3_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG3_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG4
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG4_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE4_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG4_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG4 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG4_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG4' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG4_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE4_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG4_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG5
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG5_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE5_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG5_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG5 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG5_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG5' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG5_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE5_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG5_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG6
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG6_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE6_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG6_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG6 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG6_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG6' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG6_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE6_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG6_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- CDAG7
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.CDAG7_EXPORT AS CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE7_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.CDAG7_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_CDAG7 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.CDAG7_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'CDAG7' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.CDAG7_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.CDAG_DE_TABLE7_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.CDAG7_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- FA1
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.FA1_EXPORT AS CMSUNIV_CORE_DEV_T.FA_DE_TABLE1_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.FA1_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_FA1 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.FA1_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'FA1' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.FA1_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.FA_DE_TABLE1_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.FA1_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- FA2
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.FA2_EXPORT AS CMSUNIV_CORE_DEV_T.FA_DE_TABLE2_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.FA2_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_FA2 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.FA2_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'FA2' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.FA2_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.FA_DE_TABLE2_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.FA2_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- FA3
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.FA3_EXPORT AS CMSUNIV_CORE_DEV_T.FA_DE_TABLE3_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.FA3_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_FA3 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.FA3_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'FA3' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.FA3_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.FA_DE_TABLE3_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.FA3_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- FA4
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.FA4_EXPORT AS CMSUNIV_CORE_DEV_T.FA_DE_TABLE4_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.FA4_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_FA4 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.FA4_EXPORT
   WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id
     AND Btch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'FA4' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    IF :p_src_id = '10' THEN   -- FA_HS_TABLE4_2022: its columns by position
      INSERT INTO CMSUNIV_EXPORT_DEV_T.FA4_EXPORT
              (FA_DE_Table4_2022_ID,
               EnrolleeID,
               EnrolleeFirstName,
               EnrolleeLastName,
               DateofBirth,
               EnrollmentEffectiveDate,
               EffectiveDisenrollmentDate,
               CardholderID,
               ContractID,
               PlanBenefitPackagePBP,
               UniqueKey,
               StagingCreateDate,
               Universe_Vrsn,
               Run_Ty,
               AuditDataID,
               Src_Data_Key,
               Load_Dtts,
               Btch_ID)
        SELECT FA_Table4_ID,
               EnrolleeID,
               EnrolleeFirstName,
               EnrolleeLastName,
               DateofBirth,
               EnrollmentEffectiveDate,
               EffectiveDisenrollmentDate,
               CardholderID,
               ContractID,
               PlanBenefitPackage,
               SourceUnique_ID,
               StagingCreateDate,
               Universe_Version,
               Run_Ty,
               AuditDataID,
               Source_ID,
               Load_Dtts,
               Batch_ID
          FROM CMSUNIV_CORE_DEV_T.FA_HS_TABLE4_2022
         WHERE Batch_ID = :p_data_btch_id AND CAST(Source_ID AS VARCHAR(30)) = :p_src_id;
    ELSEIF :p_src_id = '40' THEN   -- FA_CIGNAAZ_TABLE4_2022: its columns by position
      INSERT INTO CMSUNIV_EXPORT_DEV_T.FA4_EXPORT
              (FA_DE_Table4_2022_ID,
               EnrolleeID,
               EnrolleeFirstName,
               EnrolleeLastName,
               DateofBirth,
               EnrollmentEffectiveDate,
               EffectiveDisenrollmentDate,
               CardholderID,
               ContractID,
               PlanBenefitPackagePBP,
               UniqueKey,
               StagingCreateDate,
               Universe_Vrsn,
               Run_Ty,
               AuditDataID,
               Src_Data_Key,
               Load_Dtts,
               Btch_ID)
        SELECT FA_CignaAZ_TABLE4_2022_ID,
               EnrolleeID,
               EnrolleeFirstName,
               EnrolleeLastName,
               DateofBirth,
               EnrollmentEffectiveDate,
               EffectiveDisenrollmentDate,
               CardholderID,
               ContractID,
               PlanBenefitPackage,
               SourceUnique_ID,
               StagingCreateDate,
               Universe_Version,
               Run_Ty,
               AuditDataID,
               Source_ID,
               Load_Dtts,
               Batch_ID
          FROM CMSUNIV_CORE_DEV_T.FA_CIGNAAZ_TABLE4_2022
         WHERE Batch_ID = :p_data_btch_id AND CAST(Source_ID AS VARCHAR(30)) = :p_src_id;
    ELSE
      INSERT INTO CMSUNIV_EXPORT_DEV_T.FA4_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.FA_DE_TABLE4_2022 c
         WHERE c.Btch_ID = :p_data_btch_id AND CAST(c.Src_Data_Key AS VARCHAR(30)) = :p_src_id;
    END IF;
    UPDATE CMSUNIV_EXPORT_DEV_T.FA4_EXPORT SET Btch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Src_Data_Key AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;


-- ====================================================================================================
-- SNPCC1
-- ====================================================================================================

CREATE MULTISET TABLE CMSUNIV_EXPORT_DEV_T.SNPCC1_EXPORT AS CMSUNIV_CORE_DEV_T.SNPCC_HS_TABLE1_2022 WITH NO DATA;
ALTER TABLE CMSUNIV_EXPORT_DEV_T.SNPCC1_EXPORT ADD Exp_Btch_ID VARCHAR(250);

REPLACE PROCEDURE CMSUNIV_EXPORT_DEV_T.SP_EXPORT_SNPCC1 (
  IN p_btch_id VARCHAR(250), IN p_data_btch_id VARCHAR(250), IN p_src_id VARCHAR(30),
  IN p_run_ty VARCHAR(20), IN p_rpt_start DATE, IN p_rpt_end DATE)
BEGIN
  DELETE FROM CMSUNIV_EXPORT_DEV_T.SNPCC1_EXPORT
   WHERE CAST(Source_ID AS VARCHAR(30)) = :p_src_id
     AND Batch_ID IN (SELECT Btch_ID FROM HSABC_DEV.ComplianceRequestControl
                      WHERE Project_Cd = 'CMSUNIV' AND Table_Nm = 'SNPCC1' AND Src_ID = :p_src_id AND Run_Ty = :p_run_ty
                        AND Rpt_Start_Dt_Key = :p_rpt_start AND Rpt_End_Dt_Key = :p_rpt_end);
  IF :p_data_btch_id IS NOT NULL THEN
    INSERT INTO CMSUNIV_EXPORT_DEV_T.SNPCC1_EXPORT
        SELECT c.*, NULL
          FROM CMSUNIV_CORE_DEV_T.SNPCC_HS_TABLE1_2022 c
         WHERE c.Batch_ID = :p_data_btch_id AND CAST(c.Source_ID AS VARCHAR(30)) = :p_src_id;
    UPDATE CMSUNIV_EXPORT_DEV_T.SNPCC1_EXPORT SET Batch_ID = :p_btch_id, Exp_Btch_ID = :p_btch_id
     WHERE CAST(Source_ID AS VARCHAR(30)) = :p_src_id AND Exp_Btch_ID IS NULL;
  END IF;
END;
