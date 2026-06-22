with src as (
    select page_id, created_time, last_edited_time, parse_json(properties) as properties
    from {{ source('notion', 'notion_pages') }}
    where source = 'reading'
)
select
    page_id                                             as reading_id,
    properties:"Title":title[0]:plain_text::string     as title,
    properties:"Type":select:name::string              as media_type,
    properties:"Status":select:name::string            as status,
    properties:"URL":url::string                       as url,
    properties:"Notes":rich_text[0]:plain_text::string as notes,
    created_time::timestamp_tz                          as created_at,
    last_edited_time::timestamp_tz                      as updated_at
from src
