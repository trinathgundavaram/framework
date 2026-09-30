"""Report-period SQL, one statement per name (design §5.1)."""

PERIOD_SQL = {
    "SAME_DAY": """
        SELECT %(sched_dt)s::date AS rpt_start, %(sched_dt)s::date AS rpt_end""",
    "PREV_DAY": """
        SELECT (%(sched_dt)s::date - 1) AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end""",
    "PREV_N_DAYS": """
        SELECT (%(sched_dt)s::date - %(lookback_days)s::int) AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end""",
    "PREV_WEEK_SAME_DAY": """
        SELECT (%(sched_dt)s::date - 7 * %(lookback_weeks)s::int) AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end""",
    "PREV_CALENDAR_WEEK": """
        SELECT (date_trunc('week', %(sched_dt)s::date) - interval '7 days')::date AS rpt_start,
               (date_trunc('week', %(sched_dt)s::date) - interval '1 day')::date  AS rpt_end""",
    "CURRENT_CALENDAR_MONTH": """
        SELECT date_trunc('month', %(sched_dt)s::date)::date AS rpt_start,
               (date_trunc('month', %(sched_dt)s::date) + interval '1 month - 1 day')::date AS rpt_end""",
    "PREV_CALENDAR_MONTH": """
        SELECT (date_trunc('month', %(sched_dt)s::date) - interval '1 month')::date AS rpt_start,
               (date_trunc('month', %(sched_dt)s::date) - interval '1 day')::date   AS rpt_end""",
    "ROLLING_1_MONTH": """
        SELECT (%(sched_dt)s::date - interval '1 month')::date AS rpt_start, (%(sched_dt)s::date - 1) AS rpt_end""",
    "PREV_CALENDAR_QUARTER": """
        SELECT (date_trunc('quarter', %(sched_dt)s::date) - interval '3 months')::date AS rpt_start,
               (date_trunc('quarter', %(sched_dt)s::date) - interval '1 day')::date    AS rpt_end""",
    "PREV_CALENDAR_YEAR": """
        SELECT (date_trunc('year', %(sched_dt)s::date) - interval '1 year')::date AS rpt_start,
               (date_trunc('year', %(sched_dt)s::date) - interval '1 day')::date  AS rpt_end""",
}
