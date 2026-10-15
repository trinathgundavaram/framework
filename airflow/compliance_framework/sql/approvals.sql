-- Manual override templates for Teradata; run with the metadata database as default. Each must report 1 row.

-- 1. REUSE (open batch without data; :reuse_btch_id may be NULL)
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Btch_ID, Reuse_Btch_ID, Rsn_Txt, Created_By, Apprvl_Stat, Reviewed_By, Reviewed_Dtts,
   Valid_Thru_Dt_Key)
SELECT 'REUSE', Req_ID, Btch_ID, :reuse_btch_id, :reason, :me, 'APPROVED', :me, CURRENT_TIMESTAMP(6), :valid_thru
  FROM ComplianceRequestControl
 WHERE Req_ID = :req_id AND Batch_Close_Ind = 0 AND (Resolution_Ty IS NULL OR Resolution_Ty <> 'NEW_FILE')
   AND NOT EXISTS (SELECT 1 FROM ComplianceBatchOverride o WHERE o.Req_ID = :req_id AND o.Override_Ty = 'REUSE'
                      AND o.Apprvl_Stat IN ('PENDING_REVIEW', 'APPROVED'));

-- 2. LATE_ARRIVAL (closed batch without data)
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Btch_ID, Rsn_Txt, Created_By, Apprvl_Stat, Reviewed_By, Reviewed_Dtts, Valid_Thru_Dt_Key)
SELECT 'LATE_ARRIVAL', Req_ID, Btch_ID, :reason, :me, 'APPROVED', :me, CURRENT_TIMESTAMP(6), :valid_thru
  FROM ComplianceRequestControl
 WHERE Req_ID = :req_id AND Batch_Close_Ind = 1 AND Resolution_Ty = 'MISSING'
   AND NOT EXISTS (SELECT 1 FROM ComplianceBatchOverride o WHERE o.Req_ID = :req_id AND o.Override_Ty = 'LATE_ARRIVAL'
                      AND o.Apprvl_Stat IN ('PENDING_REVIEW', 'APPROVED'));

-- 3. CORRECTION (closed batch with data)
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Btch_ID, Rsn_Txt, Created_By, Apprvl_Stat, Reviewed_By, Reviewed_Dtts, Valid_Thru_Dt_Key)
SELECT 'CORRECTION', Req_ID, Btch_ID, :reason, :me, 'APPROVED', :me, CURRENT_TIMESTAMP(6), :valid_thru
  FROM ComplianceRequestControl
 WHERE Req_ID = :req_id AND Batch_Close_Ind = 1 AND Resolution_Ty IN ('NEW_FILE','CARRY_FORWARD')
   AND NOT EXISTS (SELECT 1 FROM ComplianceBatchOverride o WHERE o.Req_ID = :req_id AND o.Override_Ty = 'CORRECTION'
                      AND o.Apprvl_Stat IN ('PENDING_REVIEW', 'APPROVED'));

-- 4. Approve a pending row
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat = 'APPROVED', Reviewed_By = :me, Reviewed_Dtts = CURRENT_TIMESTAMP(6), Valid_Thru_Dt_Key = :valid_thru,
       Updated_Dtts = CURRENT_TIMESTAMP(6), Updated_By = :me
 WHERE Ovrd_ID = :ovrd_id AND Apprvl_Stat = 'PENDING_REVIEW';

-- 5. Reject a pending row
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat = 'REJECTED', Reviewed_By = :me, Reviewed_Dtts = CURRENT_TIMESTAMP(6), Rsn_Txt = :reason,
       Updated_Dtts = CURRENT_TIMESTAMP(6), Updated_By = :me
 WHERE Ovrd_ID = :ovrd_id AND Apprvl_Stat = 'PENDING_REVIEW';

-- 6. Stop an approved override (validity date in the past)
UPDATE ComplianceBatchOverride
   SET Valid_Thru_Dt_Key = :valid_thru, Updated_Dtts = CURRENT_TIMESTAMP(6), Updated_By = :me
 WHERE Ovrd_ID = :ovrd_id AND Apprvl_Stat = 'APPROVED';
