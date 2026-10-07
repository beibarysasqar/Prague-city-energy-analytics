with source as (

    select * from {{ source('golemio', 'golemio_air_quality_stations') }}

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

renamed as (

    select
        id as station_id,
        name as station_name,
        district as district_slug,
        -- GeoJSON point: coordinates are [lon, lat]
        cast(json_extract(geometry, '$.coordinates[1]') as double) as latitude,
        cast(json_extract(geometry, '$.coordinates[0]') as double) as longitude,
        cast(updated_at as timestamp with time zone) as source_updated_ts_utc,
        first_seen_date,
        load_date as last_listed_date,
        is_listed,
        false as is_inferred,
        _loaded_at as loaded_at_ts_utc
    from latest_version

),

-- Inferred members: stations that appear in the measurement history but in none of the loaded
-- snapshots (e.g. a fresh 7-day CI load after the source removed a station from its list).
-- They keep the history joinable; attributes stay unknown until the station is listed again.
inferred as (

    select distinct
        history.id as station_id,
        cast(null as varchar) as station_name,
        cast(null as varchar) as district_slug,
        cast(null as double) as latitude,
        cast(null as double) as longitude,
        cast(null as timestamp with time zone) as source_updated_ts_utc,
        cast(null as date) as first_seen_date,
        cast(null as date) as last_listed_date,
        false as is_listed,
        true as is_inferred,
        cast(null as timestamp with time zone) as loaded_at_ts_utc
    from {{ source('golemio', 'golemio_air_quality_history') }} as history
    where history.id not in (select listed.station_id from renamed as listed)

)

select * from renamed
union all
select * from inferred
