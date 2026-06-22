with src as (
    select page_id, created_time, last_edited_time, properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'pipeline_events'
)
select
    page_id                                             as event_id,
    properties:"Application":relation[0]:id::string    as application_id,
    properties:"Event":title[0]:plain_text::string     as event_name,
    properties:"From Status":rich_text[0]:plain_text::string as from_status,
    properties:"To Status":rich_text[0]:plain_text::string   as to_status,
    properties:"Trigger":rich_text[0]:plain_text::string     as trigger,
    properties:"Thread Link":url::string               as thread_link,
    try_to_timestamp_tz(properties:"Timestamp":date:start::string) as event_at,
    created_time::timestamp_tz                          as created_at
from src
