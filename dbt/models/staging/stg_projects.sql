with src as (
    select page_id, created_time, last_edited_time, parse_json(properties) as properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'projects'
)
select
    page_id                                              as project_id,
    properties:"Name":title[0]:plain_text::string       as project_name,
    {{ strip_emoji('properties:"Status":select:name::string') }}  as status,
    properties:"Check-in Frequency":select:name::string as checkin_frequency,
    try_to_date(properties:"Next Check-in":date:start::string) as next_checkin,
    try_to_date(properties:"Last Check-in":date:start::string) as last_checkin,
    properties:"Description":rich_text[0]:plain_text::string as description,
    created_time::timestamp_tz                           as created_at,
    last_edited_time::timestamp_tz                       as updated_at
from src
