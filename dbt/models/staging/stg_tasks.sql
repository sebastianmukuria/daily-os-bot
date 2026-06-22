with src as (
    select page_id, created_time, last_edited_time, properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'tasks'
)
select
    page_id                                              as task_id,
    properties:"Task":title[0]:plain_text::string       as task_name,
    {{ strip_emoji('properties:"Status":select:name::string') }}  as status,
    {{ strip_emoji('properties:"Energy":select:name::string') }}  as energy,
    properties:"Type":select:name::string               as task_type,
    try_to_date(properties:"Due Date":date:start::string) as due_date,
    coalesce(properties:"Stale":checkbox::boolean, false) as is_stale,
    properties:"Project":relation[0]:id::string         as project_id,
    properties:"Notes":rich_text[0]:plain_text::string  as notes,
    created_time::timestamp_tz                           as created_at,
    last_edited_time::timestamp_tz                       as updated_at
from src
