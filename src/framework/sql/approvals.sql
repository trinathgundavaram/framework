-- Manual approval / waiver templates (design Appendix B). Run as framework_approver.
-- Run with search_path set to the metadata schema: SET search_path TO <FRAMEWORK_METADATA_SCHEMA>;
-- Every statement must report exactly 1 row; 0 rows = candidate changed or already decided -> re-review.
-- Approve a reopen (you reviewed load :reviewed_load_id)
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat='APPROVED', Apprvd_By=:me, Apprvd_Dtts=now(), Reviewed_Load_ID=:reviewed_load_id,
       History = History || E'\n' || now() || ' APPROVED by ' || :me, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
   AND Apprvl_Stat='PENDING_REVIEW' AND Candidate_Load_ID=:reviewed_load_id;

-- Reject a pending reopen or waiver
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat='REJECTED', Rejected_By=:me, Rejected_Dtts=now(), Rejection_Rsn=:reason,
       History = History || E'\n' || now() || ' REJECTED by ' || :me || ': ' || :reason, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Apprvl_Stat='PENDING_REVIEW';

-- Request a SOURCE_WAIVER for an open batch
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, History)
SELECT 'SOURCE_WAIVER', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
       now() || ' SOURCE_WAIVER requested by ' || :me || ': ' || :reason
  FROM ComplianceRequestControl WHERE Req_ID=:req_id AND Batch_Close_Ind=0;

-- Request a RULE_WAIVER for an extract
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Rule_Ref, History)
SELECT 'RULE_WAIVER', Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, :rule_ref,
       now() || ' RULE_WAIVER requested by ' || :me || ': ' || :reason
  FROM ComplianceExtractControl WHERE Extract_ID=:extract_id AND Trigger_Stat <> 'TRIGGERED';

-- Request a CARRY_FORWARD for an open batch with no data (run type must have Carry_Fwd_Ind = 1).
-- :reuse_btch_id may be NULL: the framework then reuses the latest earlier closed batch that has data.
INSERT INTO ComplianceBatchOverride
  (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
   Btch_ID, Reuse_Btch_ID, History)
SELECT 'CARRY_FORWARD', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
       Btch_ID, :reuse_btch_id, now() || ' CARRY_FORWARD requested by ' || :me || ': ' || :reason
  FROM ComplianceRequestControl WHERE Req_ID=:req_id AND Batch_Close_Ind=0 AND Current_Load_ID IS NULL;

-- Approve a waiver or carry-forward
UPDATE ComplianceBatchOverride
   SET Apprvl_Stat='APPROVED', Apprvd_By=:me, Apprvd_Dtts=now(),
       History = History || E'\n' || now() || ' APPROVED by ' || :me, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER','CARRY_FORWARD') AND Apprvl_Stat='PENDING_REVIEW';

-- Revoke an approved waiver or carry-forward (only before the extract is triggered)
UPDATE ComplianceBatchOverride o
   SET Apprvl_Stat='REVOKED', Revoked_By=:me, Revoked_Dtts=now(), Revocation_Rsn=:reason,
       History = History || E'\n' || now() || ' REVOKED by ' || :me || ': ' || :reason, Updated_Dtts=now()
  FROM ComplianceExtractControl e
 WHERE o.Ovrd_ID=:ovrd_id AND o.Extract_ID=e.Extract_ID AND o.Apprvl_Stat='APPROVED'
   AND o.Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER','CARRY_FORWARD') AND e.Trigger_Stat <> 'TRIGGERED';
