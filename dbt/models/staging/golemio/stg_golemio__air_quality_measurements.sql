-- One row per station x measured hour x pollutant.
-- `updated_at` is the publication time (10-50 min after the hour), so a publication belongs to
-- the hour that ended at date_trunc('hour', updated_at).
-- Re-publications (HH:40) replace earlier ones.

with source as (

    select * from {{ source('golemio', 'golemio_air_quality_history') }}

),

publications as (

    select
        id as station_id,
        cast(updated_at as timestamp with time zone) as published_ts_utc,
        {{ dbt.date_trunc('hour', 'cast(updated_at as timestamp with time zone)') }}
        - interval '1 hour' as measured_hour_start_ts_utc,
        json_extract_string(measurement, '$.AQ_hourly_index') as aq_hourly_index,
        measurement,
        _loaded_at as loaded_at_ts_utc
    from source

),

latest_publication as (

    select *
    from publications
    qualify row_number() over (
        partition by station_id, measured_hour_start_ts_utc
        order by published_ts_utc desc, loaded_at_ts_utc desc
    ) = 1

),

components as (

    select
        station_id,
        measured_hour_start_ts_utc,
        aq_hourly_index,
        published_ts_utc,
        loaded_at_ts_utc,
        unnest(json_extract(measurement, '$.components[*]')) as component
    from latest_publication

),

final as (

    select
        station_id,
        measured_hour_start_ts_utc,
        json_extract_string(component, '$.type') as pollutant,
        cast(json_extract(component, '$.averaged_time.value') as double) as value_ugm3,
        cast(
            json_extract_string(component, '$.averaged_time.averaged_hours') as integer
        ) as averaging_hours,
        aq_hourly_index,
        timezone('Europe/Prague', measured_hour_start_ts_utc) as measured_hour_start_local,
        published_ts_utc,
        loaded_at_ts_utc
    from components

)

select * from final
