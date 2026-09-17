-- Manual approval / waiver templates (design Appendix B). Run as framework_approver.
-- Every statement must report exactly 1 row; 0 rows = candidate changed or already decided -> re-review.
-- Approve a reopen (you reviewed load :reviewed_load_id)
UPDATE cms_compliance.ComplianceBatchOverride
   SET Apprvl_Stat='APPROVED', Apprvd_By=:me, Apprvd_Dtts=now(), Reviewed_Load_ID=:reviewed_load_id,
       History = History || E'\n' || now() || ' APPROVED by ' || :me, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Override_Ty IN ('LATE_ARRIVAL_REOPEN','CORRECTION_REOPEN')
   AND Apprvl_Stat='PENDING_REVIEW' AND Candidate_Load_ID=:reviewed_load_id;

-- Reject a pending reopen or waiver
UPDATE cms_compliance.ComplianceBatchOverride
   SET Apprvl_Stat='REJECTED', Rejected_By=:me, Rejected_Dtts=now(), Rejection_Rsn=:reason,
       History = History || E'\n' || now() || ' REJECTED by ' || :me || ': ' || :reason, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Apprvl_Stat='PENDING_REVIEW';

-- Request a SOURCE_WAIVER for an open batch
INSERT INTO cms_compliance.ComplianceBatchOverride
  (Override_Ty, Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, History)
SELECT 'SOURCE_WAIVER', Req_ID, Extract_ID, Project_Cd, Table_Nm, Src_Cd, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key,
       now() || ' SOURCE_WAIVER requested by ' || :me || ': ' || :reason
  FROM cms_compliance.ComplianceRequestControl WHERE Req_ID=:req_id AND Batch_Close_Ind=0;

-- Request a RULE_WAIVER for an extract
INSERT INTO cms_compliance.ComplianceBatchOverride
  (Override_Ty, Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, Rule_Ref, History)
SELECT 'RULE_WAIVER', Extract_ID, Project_Cd, Table_Nm, Run_Ty, Rpt_Start_Dt_Key, Rpt_End_Dt_Key, :rule_ref,
       now() || ' RULE_WAIVER requested by ' || :me || ': ' || :reason
  FROM cms_compliance.ComplianceExtractControl WHERE Extract_ID=:extract_id AND Trigger_Stat <> 'TRIGGERED';

-- Approve a waiver
UPDATE cms_compliance.ComplianceBatchOverride
   SET Apprvl_Stat='APPROVED', Apprvd_By=:me, Apprvd_Dtts=now(),
       History = History || E'\n' || now() || ' APPROVED by ' || :me, Updated_Dtts=now()
 WHERE Ovrd_ID=:ovrd_id AND Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') AND Apprvl_Stat='PENDING_REVIEW';

-- Revoke an approved waiver (only before the extract is triggered)
UPDATE cms_compliance.ComplianceBatchOverride o
   SET Apprvl_Stat='REVOKED', Revoked_By=:me, Revoked_Dtts=now(), Revocation_Rsn=:reason,
       History = History || E'\n' || now() || ' REVOKED by ' || :me || ': ' || :reason, Updated_Dtts=now()
  FROM cms_compliance.ComplianceExtractControl e
 WHERE o.Ovrd_ID=:ovrd_id AND o.Extract_ID=e.Extract_ID AND o.Apprvl_Stat='APPROVED'
   AND o.Override_Ty IN ('SOURCE_WAIVER','RULE_WAIVER') AND e.Trigger_Stat <> 'TRIGGERED';
