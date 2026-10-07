-- Counters carry no district in the API, so the district is derived with a point-in-polygon
-- join against the district boundaries.

with source as (

    select * from {{ source('golemio', 'golemio_bicycle_counters') }}

),

-- Latest version of every record ever listed. Records the source removes from its list stay in the
-- reference with is_listed = false, so their history (measurements, detections) keeps a parent.
latest_version as (

    select
        *,
        min(load_date) over (partition by id) as first_seen_date,
        load_date = max(load_date) over () as is_listed
    from source
    qualify row_number() over (partition by id order by load_date desc) = 1

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
        first_seen_date,
        load_date as last_listed_date,
        is_listed,
        _loaded_at as loaded_at_ts_utc
    from latest_version

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
        renamed.first_seen_date,
        renamed.last_listed_date,
        renamed.is_listed,
        false as is_inferred,
        renamed.loaded_at_ts_utc
    from renamed
    left join districts
        on {{ st_contains_geojson('districts.geometry_geojson', 'renamed.geometry_geojson') }}

),

-- Inferred members: counters that appear in the detections but in none of the loaded snapshots.
inferred as (

    select distinct
        detections.locations_id as counter_id,
        cast(null as varchar) as counter_name,
        cast(null as varchar) as cycle_route,
        cast(null as double) as latitude,
        cast(null as double) as longitude,
        cast(null as varchar) as district_slug,
        cast(null as timestamp with time zone) as source_updated_ts_utc,
        cast(null as date) as first_seen_date,
        cast(null as date) as last_listed_date,
        false as is_listed,
        true as is_inferred,
        cast(null as timestamp with time zone) as loaded_at_ts_utc
    from {{ source('golemio', 'golemio_bicycle_detections') }} as detections
    where detections.locations_id not in (select listed.counter_id from renamed as listed)

)

select * from with_district
union all
select * from inferred
