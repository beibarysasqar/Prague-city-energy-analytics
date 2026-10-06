with source as (

    select * from {{ source('golemio', 'golemio_air_quality_stations') }}

),

latest_snapshot as (

    select *
    from source
    qualify load_date = max(load_date) over ()

),

renamed as (

    select
        id as station_id,
        name as station_name,
        district as district_slug,
        -- GeoJSON point: coordinates are [lon, lat]
        cast(json_extract(geometry, '$.coordinates[1]') as double) as latitude,
        cast(json_extract(geometry, '$.coordinates[0]') as double) as longitude,
        cast(updated_at as timestamp with time zone) as source_updated_ts_utc,
        load_date as snapshot_date,
        _loaded_at as loaded_at_ts_utc
    from latest_snapshot

)

select * from renamed
