with source as (

    select * from {{ source('golemio', 'golemio_city_districts') }}

),

latest_snapshot as (

    select *
    from source
    qualify load_date = max(load_date) over ()

),

renamed as (

    select
        cast(id as integer) as district_id,
        name as district_name,
        slug as district_slug,
        geometry as geometry_geojson,
        cast(updated_at as timestamp with time zone) as source_updated_ts_utc,
        _loaded_at as loaded_at_ts_utc
    from latest_snapshot

)

select * from renamed
