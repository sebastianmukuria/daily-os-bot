-- One row per habit with current streak + recency.
with h as (
    select * from {{ ref('stg_habits') }}
)
select
    habit_id,
    habit_name,
    is_active,
    cadence,
    coalesce(streak, 0)                                  as streak,
    last_done,
    datediff('day', last_done, current_date())           as days_since_done,
    created_at,
    updated_at
from h
