-- One row per event, already carrying its user and product attributes, so a funnel
-- question is a single query with no joins to remember (spec 0002).

with events as (

    select * from {{ ref('stg_raw__events') }}

),

users as (

    select * from {{ ref('stg_raw__users') }}

),

products as (

    select * from {{ ref('stg_raw__products') }}

),

-- First seen wins: the copy from the lowest load, and within a load the lowest line.
-- The gap between this count and stg_raw__events' is the duplicate count feature 5 wants.
ranked as (

    select
        *,
        row_number() over (
            partition by event_id order by load_id, line_number
        ) as copy_rank

    from events

),

deduplicated as (

    select * exclude (copy_rank, line_number)
    from ranked
    where copy_rank = 1

),

-- Reference attributes resolve as of the load: the version whose load_id is the
-- greatest at or before the event's. `asof left join` is what keeps an event whose
-- user or product never arrived, with its attributes null.
joined as (

    select
        events.event_id,
        events.session_id,
        events.user_id,
        events.event_type,
        events.event_time,
        events.product_id,
        events.load_id,
        users.country as user_country,
        users.city as user_city,
        users.device_type as user_device_type,
        users.created_at as user_created_at,
        products.category as product_category,
        products.price as product_price

    from deduplicated as events

    asof left join users
        on events.user_id = users.user_id
        and events.load_id >= users.load_id

    asof left join products
        on events.product_id = products.product_id
        and events.load_id >= products.load_id

)

select * from joined
