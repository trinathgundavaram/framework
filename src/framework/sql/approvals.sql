-- Manual override templates (design Appendix B). Run as framework_approver with
--   SET search_path TO <FRAMEWORK_METADATA_SCHEMA>;
-- Every statement must report exactly 1 row; 0 rows = the batch changed or the row was already
-- decided -> re-check before trying again.
--
-- One shape covers all three cases. An approval works until Valid_Thru_Dt_Key (inclusive);
-- to stop it early, set that date in the past (there is no separate revoke).

-- 1. REUSE: an open batch with no data reuses an earlier batch's data.
--    :reuse_btch_id may be NULL -> the framework picks the latest earlier closed batch that has data.
--    Only for run types with Carry_Fwd_Ind = 1; `process-decisions` applies it.
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
   Btch_ID, Reuse_Btch_ID, Rsn, Requested_By, Apprvl_Stat, Apprvd_By, Apprvd_Dtts, Valid_Thru_Dt_Key)
SELECT 'REUSE', Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
       Btch_ID, :reuse_btch_id, :reason, :me, 'APPROVED', :me, now(), :valid_thru
  FROM ComplianceRequestControl
 WHERE Req_ID = :req_id AND Batch_Close_Ind = 0 AND Resolution_Ty IS DISTINCT FROM 'NEW_FILE';

-- 2. LATE_ARRIVAL: a file may still be promoted into a closed batch that has no data.
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
   Btch_ID, Rsn, Requested_By, Apprvl_Stat, Apprvd_By, Apprvd_Dtts, Valid_Thru_Dt_Key)
SELECT 'LATE_ARRIVAL', Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
       Btch_ID, :reason, :me, 'APPROVED', :me, now(), :valid_thru
  FROM ComplianceRequestControl
 WHERE Req_ID = :req_id AND Batch_Close_Ind = 1 AND Resolution_Ty = 'MISSING';

-- 3. CORRECTION: a corrected file may replace the data of a closed batch.
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
   Btch_ID, Rsn, Requested_By, Apprvl_Stat, Apprvd_By, Apprvd_Dtts, Valid_Thru_Dt_Key)
SELECT 'CORRECTION', Req_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Req_Dt_Key,
       Btch_ID, :reason, :me, 'APPROVED', :me, now(), :valid_thru
  FROM ComplianceRequestControl
 WHERE Req_ID = :req_id AND Batch_Close_Ind = 1 AND Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD');

-- 4. Approve a row someone else requested (Apprvl_Stat = 'PENDING_REVIEW').
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat = 'APPROVED', Apprvd_By = :me, Apprvd_Dtts = now(), Valid_Thru_Dt_Key = :valid_thru,
       Updated_Dtts = now(), Updated_By = :me
 WHERE Ovrd_ID = :ovrd_id AND Apprvl_Stat = 'PENDING_REVIEW';

-- 5. Reject a pending row.
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat = 'REJECTED', Rejected_By = :me, Rejected_Dtts = now(), Rsn = :reason,
       Updated_Dtts = now(), Updated_By = :me
 WHERE Ovrd_ID = :ovrd_id AND Apprvl_Stat = 'PENDING_REVIEW';

-- 6. Stop an approved override early (this replaces "revoke"): move its validity date into the past.
--    A REUSE that is still applied is removed by the next `process-decisions` run.
UPDATE ComplianceBatchOverride
   SET Valid_Thru_Dt_Key = :valid_thru, Updated_Dtts = now(), Updated_By = :me
 WHERE Ovrd_ID = :ovrd_id AND Apprvl_Stat = 'APPROVED';
