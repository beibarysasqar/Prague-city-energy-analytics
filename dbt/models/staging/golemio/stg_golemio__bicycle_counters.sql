-- Counters carry no district in the API, so the district is derived with a point-in-polygon
-- join against the district boundaries.

with source as (

    select * from {{ source('golemio', 'golemio_bicycle_counters') }}

),

latest_snapshot as (

    select *
    from source
    qualify load_date = max(load_date) over ()

),

districts as (

    select * from {{ ref('stg_golemio__city_districts') }}

),

renamed as (

    select
        id as counter_id,
        name as counter_name,
        route as cycle_route,
        geometry as geometry_geojson,
        cast(json_extract(geometry, '$.coordinates[1]') as double) as latitude,
        cast(json_extract(geometry, '$.coordinates[0]') as double) as longitude,
        cast(updated_at as timestamp with time zone) as source_updated_ts_utc,
        load_date as snapshot_date,
        _loaded_at as loaded_at_ts_utc
    from latest_snapshot

),

with_district as (

    select
        renamed.counter_id,
        renamed.counter_name,
        renamed.cycle_route,
        renamed.latitude,
        renamed.longitude,
        districts.district_slug,
        renamed.source_updated_ts_utc,
        renamed.snapshot_date,
        renamed.loaded_at_ts_utc
    from renamed
    left join districts
        on {{ st_contains_geojson('districts.geometry_geojson', 'renamed.geometry_geojson') }}

)

select * from with_district
