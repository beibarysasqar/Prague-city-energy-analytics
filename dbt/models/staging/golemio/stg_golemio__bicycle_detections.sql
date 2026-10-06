-- 5-minute counts per counter direction. A direction id can belong to two counters, so the key is
-- (counter_id, direction_id, interval_start_ts_utc).

with source as (

    select * from {{ source('golemio', 'golemio_bicycle_detections') }}

),

renamed as (

    select
        locations_id as counter_id,
        id as direction_id,
        cast(measured_from as timestamp with time zone) as interval_start_ts_utc,
        cast(measured_to as timestamp with time zone) as interval_end_ts_utc,
        cast(value as integer) as bike_count,
        cast(value_pedestrians as integer) as pedestrian_count,
        cast(measurement_count as integer) as measurement_count,
        _loaded_at as loaded_at_ts_utc
    from source

),

deduplicated as (

    select *
    from renamed
    qualify row_number() over (
        partition by counter_id, direction_id, interval_start_ts_utc
        order by loaded_at_ts_utc desc
    ) = 1

)

select * from deduplicated
