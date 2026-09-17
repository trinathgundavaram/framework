SELECT (date_trunc('quarter', %(sched_dt)s::date) - interval '3 months')::date AS rpt_start,
       (date_trunc('quarter', %(sched_dt)s::date) - interval '1 day')::date    AS rpt_end
