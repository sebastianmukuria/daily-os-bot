with src as (
    select page_id, created_time, last_edited_time, properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'job_pipeline'
)
select
    page_id                                             as application_id,
    properties:"Company":title[0]:plain_text::string   as company,
    properties:"Role":rich_text[0]:plain_text::string  as role,
    properties:"Status":select:name::string            as status,
    properties:"Location":select:name::string          as location,
    properties:"Source":select:name::string            as source_channel,
    properties:"Confidence":select:name::string        as confidence,
    try_to_date(properties:"Applied date":date:start::string)    as applied_date,
    try_to_date(properties:"Last activity":date:start::string)   as last_activity_date,
    try_to_date(properties:"Next action due":date:start::string) as next_action_due,
    properties:"Posting URL":url::string               as posting_url,
    properties:"Stage detail":rich_text[0]:plain_text::string as stage_detail,
    properties:"Comp posted":rich_text[0]:plain_text::string  as comp_posted,
    properties:"Next action":rich_text[0]:plain_text::string  as next_action,
    properties:"Materials":rich_text[0]:plain_text::string    as materials,
    properties:"Gmail thread IDs":rich_text[0]:plain_text::string as gmail_thread_ids,
    created_time::timestamp_tz                          as created_at,
    last_edited_time::timestamp_tz                      as updated_at
from src
