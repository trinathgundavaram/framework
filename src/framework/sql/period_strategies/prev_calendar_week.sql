SELECT (date_trunc('week', %(sched_dt)s::date) - interval '7 days')::date AS rpt_start,
       (date_trunc('week', %(sched_dt)s::date) - interval '1 day')::date  AS rpt_end
