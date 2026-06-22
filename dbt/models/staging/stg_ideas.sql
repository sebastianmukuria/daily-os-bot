with src as (
    select page_id, created_time, last_edited_time, parse_json(properties) as properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'ideas'
)
select
    page_id                                             as idea_id,
    properties:"Idea":title[0]:plain_text::string      as idea,
    properties:"Category":select:name::string          as category,
    properties:"Notes":rich_text[0]:plain_text::string as notes,
    created_time::timestamp_tz                          as created_at,
    last_edited_time::timestamp_tz                      as updated_at
from src
