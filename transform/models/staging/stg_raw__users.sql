-- One row per (user_id, load_id): every loaded version of a user is kept, so an
-- event can resolve the version that was current as of its own load (spec 0002).

with source as (

    select * from {{ source('raw', 'users') }}

),

valid_json as (

    select * from source
    where json_valid(raw_line)

),

extracted as (

    select
        json_extract_string(raw_line, '$.user_id') as user_id,
        json_extract_string(raw_line, '$.country') as country,
        json_extract_string(raw_line, '$.city') as city,
        json_extract_string(raw_line, '$.device_type') as device_type,
        json_extract_string(raw_line, '$.created_at') as created_at_text,
        load_id

    from valid_json

),

typed as (

    select
        *,
        try_cast(created_at_text as timestamp) as created_at

    from extracted

)

select
    user_id,
    country,
    city,
    device_type,
    created_at,
    load_id

from typed
where not (created_at_text is not null and created_at is null)
