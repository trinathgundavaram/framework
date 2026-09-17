SELECT (%(sched_dt)s::date - interval '1 month')::date AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end
