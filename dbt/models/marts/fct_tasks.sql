-- One row per task with productivity-friendly flags.
with t as (
    select * from {{ ref('stg_tasks') }}
)
select
    task_id,
    task_name,
    status,
    energy,
    task_type,
    due_date,
    project_id,
    is_stale,
    status = 'Done'                                          as is_done,
    due_date is not null and due_date < current_date()
        and status != 'Done'                                as is_overdue,
    datediff('day', created_at::date, current_date())        as age_days,
    created_at,
    updated_at
from t
