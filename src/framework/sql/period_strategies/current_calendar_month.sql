SELECT date_trunc('month', %(sched_dt)s::date)::date AS rpt_start,
       (date_trunc('month', %(sched_dt)s::date) + interval '1 month - 1 day')::date AS rpt_end
