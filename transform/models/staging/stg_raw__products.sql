-- One row per (product_id, load_id): every loaded version of a product is kept, so an
-- event can resolve the version that was current as of its own load (spec 0002).

with source as (

    select * from {{ source('raw', 'products') }}

),

valid_json as (

    select * from source
    where json_valid(raw_line)

),

extracted as (

    select
        json_extract_string(raw_line, '$.product_id') as product_id,
        json_extract_string(raw_line, '$.category') as category,
        json_extract_string(raw_line, '$.price') as price_text,
        load_id

    from valid_json

),

typed as (

    select
        *,
        -- Rounds rather than rejects: '12.345' becomes 12.35. Only a non-numeric
        -- price is a cast failure.
        try_cast(price_text as decimal(10, 2)) as price

    from extracted

)

select
    product_id,
    category,
    price,
    load_id

from typed
where not (price_text is not null and price is null)
