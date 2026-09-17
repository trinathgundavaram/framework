SELECT (%(sched_dt)s::date - %(lookback_days)s::int) AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end
