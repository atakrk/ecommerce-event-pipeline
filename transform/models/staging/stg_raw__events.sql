-- One row per raw event line that parsed and cast. Deliberately not deduplicated:
-- staging stays one to one with raw, so a row count gap means exactly one thing
-- (a parse or cast failure). Deduplication is fct_events' job (spec 0002).

with source as (

    select * from {{ source('raw', 'events') }}

),

-- json_extract_string raises on invalid JSON instead of returning null, so malformed
-- lines have to be filtered out before anything tries to read a field.
valid_json as (

    select * from source
    where json_valid(raw_line)

),

extracted as (

    select
        json_extract_string(raw_line, '$.event_id') as event_id,
        json_extract_string(raw_line, '$.session_id') as session_id,
        json_extract_string(raw_line, '$.user_id') as user_id,
        json_extract_string(raw_line, '$.event_type') as event_type,
        json_extract_string(raw_line, '$.event_time') as event_time_text,
        json_extract_string(raw_line, '$.product_id') as product_id,
        load_id,
        line_number

    from valid_json

),

typed as (

    select
        *,
        try_cast(event_time_text as timestamp) as event_time

    from extracted

)

select
    event_id,
    session_id,
    user_id,
    event_type,
    event_time,
    product_id,
    load_id,
    line_number

from typed
-- A present value that will not cast is dropped; a JSON null is kept as null and
-- left to the not_null tests, because semantic validation belongs to feature 2.
where not (event_time_text is not null and event_time is null)
