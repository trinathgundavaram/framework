SELECT (date_trunc('month', %(sched_dt)s::date) - interval '1 month')::date AS rpt_start,
       (date_trunc('month', %(sched_dt)s::date) - interval '1 day')::date   AS rpt_end
