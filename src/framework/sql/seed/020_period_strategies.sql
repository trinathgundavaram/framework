-- Period strategies shipped with the package (src/framework/batches/sql/period_strategies/*.sql). Safe to re-run.
INSERT INTO CompliancePeriodStrategy
  (Period_Strategy_Cd, Sql_File_Nm, Requires_Lookback_Days_Ind, Requires_Lookback_Weeks_Ind, Strategy_Desc) VALUES
  ('SAME_DAY',               'same_day.sql',               0, 0, 'Report period = the scheduled date'),
  ('PREV_DAY',               'prev_day.sql',               0, 0, 'Report period = the day before the scheduled date'),
  ('PREV_N_DAYS',            'prev_n_days.sql',            1, 0, 'The N days ending the day before the scheduled date'),
  ('PREV_WEEK_SAME_DAY',     'prev_week_same_day.sql',     0, 1, 'From the same weekday N weeks earlier to the day before the scheduled date'),
  ('PREV_CALENDAR_WEEK',     'prev_calendar_week.sql',     0, 0, 'Previous Monday-Sunday week'),
  ('CURRENT_CALENDAR_MONTH', 'current_calendar_month.sql', 0, 0, 'Calendar month containing the scheduled date'),
  ('PREV_CALENDAR_MONTH',    'prev_calendar_month.sql',    0, 0, 'Calendar month before the scheduled date'),
  ('ROLLING_1_MONTH',        'rolling_1_month.sql',        0, 0, 'Same day previous month to the day before the scheduled date'),
  ('PREV_CALENDAR_QUARTER',  'prev_calendar_quarter.sql',  0, 0, 'Calendar quarter before the scheduled date'),
  ('PREV_CALENDAR_YEAR',     'prev_calendar_year.sql',     0, 0, 'Calendar year before the scheduled date')
ON CONFLICT (Period_Strategy_Cd) DO UPDATE SET Sql_File_Nm = EXCLUDED.Sql_File_Nm,
  Requires_Lookback_Days_Ind = EXCLUDED.Requires_Lookback_Days_Ind,
  Requires_Lookback_Weeks_Ind = EXCLUDED.Requires_Lookback_Weeks_Ind, Strategy_Desc = EXCLUDED.Strategy_Desc;
