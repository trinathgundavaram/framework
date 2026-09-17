SELECT (date_trunc('year', %(sched_dt)s::date) - interval '1 year')::date AS rpt_start,
       (date_trunc('year', %(sched_dt)s::date) - interval '1 day')::date  AS rpt_end
