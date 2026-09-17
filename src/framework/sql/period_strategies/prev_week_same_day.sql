SELECT (%(sched_dt)s::date - 7 * %(lookback_weeks)s::int) AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end
