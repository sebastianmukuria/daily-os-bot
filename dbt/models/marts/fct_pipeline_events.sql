-- One row per status-transition event, enriched with the application it belongs to.
with events as (
    select * from {{ ref('stg_pipeline_events') }}
),
apps as (
    select application_id, company, role from {{ ref('stg_applications') }}
)
select
    e.event_id,
    e.application_id,
    a.company,
    a.role,
    e.event_name,
    e.from_status,
    e.to_status,
    e.event_trigger,
    e.thread_link,
    e.event_at,
    row_number() over (
        partition by e.application_id order by e.event_at
    ) as event_seq
from events e
left join apps a on a.application_id = e.application_id
