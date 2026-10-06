with source as (

    select * from {{ source('open_meteo', 'open_meteo_weather_hourly') }}

),

renamed as (

    select
        station_id,
        -- `time` is naive ISO text requested with timezone=UTC
        cast(cast(time as timestamp) as timestamp with time zone) as hour_start_ts_utc,
        cast(temperature_2m as double) as temperature_c,
        cast(relative_humidity_2m as double) as relative_humidity_pct,
        cast(precipitation as double) as precipitation_mm,
        cast(wind_speed_10m as double) as wind_speed_kmh,
        cast(wind_speed_10m as double) / 3.6 as wind_speed_ms,
        cast(wind_direction_10m as double) as wind_direction_deg,
        cast(latitude as double) as grid_latitude,
        cast(longitude as double) as grid_longitude,
        cast(elevation as double) as grid_elevation_m,
        _loaded_at as loaded_at_ts_utc
    from source

),

deduplicated as (

    select
        *,
        timezone('Europe/Prague', hour_start_ts_utc) as hour_start_local
    from renamed
    qualify row_number() over (
        partition by station_id, hour_start_ts_utc order by loaded_at_ts_utc desc
    ) = 1

)

select * from deduplicated
