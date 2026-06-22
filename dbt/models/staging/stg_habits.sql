with src as (
    select page_id, created_time, last_edited_time, properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'habits'
)
select
    page_id                                             as habit_id,
    properties:"Name":title[0]:plain_text::string      as habit_name,
    coalesce(properties:"Active":checkbox::boolean, false) as is_active,
    properties:"Cadence":select:name::string           as cadence,
    properties:"Streak":number::int                    as streak,
    try_to_date(properties:"Last Done":date:start::string) as last_done,
    properties:"Notes":rich_text[0]:plain_text::string as notes,
    created_time::timestamp_tz                          as created_at,
    last_edited_time::timestamp_tz                      as updated_at
from src
