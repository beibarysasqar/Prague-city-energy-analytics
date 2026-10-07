with counters as (

    select * from {{ ref('stg_golemio__bicycle_counters') }}

),

districts as (

    select
        district_key,
        district_slug
    from {{ ref('dim_district') }}

),

final as (

    select
        {{ dbt_utils.generate_surrogate_key(['counters.counter_id']) }} as counter_key,
        counters.counter_id,
        counters.counter_name,
        counters.cycle_route,
        coalesce(districts.district_key, '-1') as district_key,
        counters.district_slug,
        counters.latitude,
        counters.longitude,
        counters.is_listed,
        counters.is_inferred,
        counters.source_updated_ts_utc
    from counters
    left join districts on counters.district_slug = districts.district_slug

)

select * from final
