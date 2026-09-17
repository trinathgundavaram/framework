-- =============================================================================
-- PROVISIONAL Req_Stat values - REPLACE when the final list is supplied (open question Q-01).
-- The code never uses these literals: it resolves every status through Abstract_State,
-- so replacing this file (one active value per abstract state + transitions) needs no code change.
-- =============================================================================
INSERT INTO cms_compliance.ComplianceRequestStatus (Req_Stat, Req_Stat_Desc, Abstract_State, Is_Closed_Ind) VALUES
  ('PENDING',                  'Open, no usable data yet',                         'S_AWAITING',           0),
  ('RULES_PASSED',             'Open, staged and validated (transient)',           'S_VALIDATED',          0),
  ('PROMOTED',                 'Open, current data promoted to core',              'S_PROMOTED',           0),
  ('EXCEPTION_PENDING',        'Open, latest file failed validation',              'S_EXCEPTION',          0),
  ('COMPLETED',                'Closed by extract trigger, data present',          'S_COMPLETE',           1),
  ('COMPLETED_WITH_EXCEPTION', 'Closed by extract trigger while in exception',     'S_COMPLETE_EXCEPTION', 1),
  ('DATA_NOT_PROVIDED',        'Closed by extract trigger, no data',               'S_NOT_PROVIDED',       1)
ON CONFLICT (Req_Stat) DO NOTHING;

INSERT INTO cms_compliance.ComplianceRequestStatusTransition (From_Req_Stat, To_Req_Stat, Trigger_Cd) VALUES
  ('PENDING',                  'RULES_PASSED',       'FILE_PASSED'),
  ('PROMOTED',                 'RULES_PASSED',       'FILE_PASSED'),
  ('EXCEPTION_PENDING',        'RULES_PASSED',       'FILE_PASSED'),
  ('RULES_PASSED',             'PROMOTED',           'CORE_SWAP'),
  ('PENDING',                  'PROMOTED',           'CORE_SWAP'),
  ('EXCEPTION_PENDING',        'PROMOTED',           'CORE_SWAP'),
  ('PENDING',                  'EXCEPTION_PENDING',  'FILE_FAILED'),
  ('PROMOTED',                 'EXCEPTION_PENDING',  'FILE_FAILED'),
  ('RULES_PASSED',             'EXCEPTION_PENDING',  'FILE_FAILED'),
  ('PROMOTED',                 'COMPLETED',          'EXTRACT_TRIGGERED'),
  ('EXCEPTION_PENDING',        'COMPLETED_WITH_EXCEPTION', 'EXTRACT_TRIGGERED'),
  ('PENDING',                  'DATA_NOT_PROVIDED',  'EXTRACT_TRIGGERED'),
  ('COMPLETED',                'COMPLETED',          'REOPEN_PROMOTED'),
  ('COMPLETED_WITH_EXCEPTION', 'COMPLETED',          'REOPEN_PROMOTED'),
  ('DATA_NOT_PROVIDED',        'COMPLETED',          'REOPEN_PROMOTED')
ON CONFLICT DO NOTHING;
