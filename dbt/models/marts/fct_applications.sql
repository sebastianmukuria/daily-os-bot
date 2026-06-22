-- One row per job application, with derived lifecycle flags + age.
with apps as (
    select * from {{ ref('stg_applications') }}
)
select
    application_id,
    company,
    role,
    status,
    location,
    source_channel,
    confidence,
    applied_date,
    last_activity_date,
    next_action_due,
    posting_url,
    datediff('day', applied_date, coalesce(last_activity_date, current_date())) as days_active,
    status in ('Rejected', 'Withdrawn', 'Ghosted')                              as is_closed,
    status = 'Offer'                                                            as is_offer,
    status = 'Rejected'                                                         as is_rejected,
    created_at,
    updated_at
from apps
