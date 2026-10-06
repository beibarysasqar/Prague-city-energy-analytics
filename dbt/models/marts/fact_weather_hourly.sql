{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key=['station_id', 'hour_start_ts_utc'],
    on_schema_change='append_new_columns'
) }}

with weather as (

    select *
    from {{ ref('stg_open_meteo__weather_hourly') }}
    {{ incremental_lookback('hour_start_ts_utc') }}

),

stations as (

    select * from {{ ref('dim_station') }}

),

final as (

    select
        weather.station_id,
        weather.hour_start_ts_utc,
        stations.station_key,
        coalesce(stations.district_key, '-1') as district_key,
        {{ to_date_key(prague_local_date('weather.hour_start_ts_utc')) }} as date_key,
        weather.hour_start_local,
        weather.temperature_c,
        weather.relative_humidity_pct,
        weather.precipitation_mm,
        weather.wind_speed_ms,
        weather.wind_speed_kmh,
        weather.wind_direction_deg
    from weather
    left join stations
        on
            weather.station_id = stations.station_id
            and weather.hour_start_ts_utc >= stations.valid_from_ts_utc
            and weather.hour_start_ts_utc < stations.valid_to_ts_utc

)

select * from final
