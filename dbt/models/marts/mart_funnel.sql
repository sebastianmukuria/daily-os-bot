-- Job-search funnel: how many applications reached each stage, with conversion.
-- "Reached" = furthest forward stage ever attained, derived from the application's
-- current status AND its full transition history (so a Rejected app still counts
-- toward every stage it passed through before the rejection).
with stages as (
    select * from (values
        ('Applied', 1),
        ('Recruiter Screen', 2),
        ('Interviewing', 3),
        ('Final Round', 4),
        ('Offer', 5)
    ) as t(stage, stage_rank)
),

app_current as (
    select a.application_id, s.stage_rank as current_rank
    from {{ ref('fct_applications') }} a
    left join stages s on s.stage = a.status
),

event_ranks as (
    select e.application_id, max(s.stage_rank) as event_rank
    from {{ ref('fct_pipeline_events') }} e
    join stages s on s.stage = e.to_status
    group by e.application_id
),

app_max as (
    select
        c.application_id,
        greatest(coalesce(c.current_rank, 1), coalesce(er.event_rank, 1)) as max_rank
    from app_current c
    left join event_ranks er on er.application_id = c.application_id
),

funnel as (
    select
        s.stage,
        s.stage_rank,
        count(distinct case when m.max_rank >= s.stage_rank then m.application_id end) as applications_reached
    from stages s
    cross join app_max m
    group by s.stage, s.stage_rank
)

select
    stage,
    stage_rank,
    applications_reached,
    round(100.0 * applications_reached
          / nullif(max(applications_reached) over (), 0), 1) as pct_of_applied,
    round(100.0 * applications_reached
          / nullif(lag(applications_reached) over (order by stage_rank), 0), 1) as conversion_from_prev_pct
from funnel
order by stage_rank
