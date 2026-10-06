{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key=['station_id', 'hour_start_ts_utc', 'pollutant'],
    on_schema_change='append_new_columns'
) }}

with measurements as (

    select *
    from {{ ref('stg_golemio__air_quality_measurements') }}
    {{ incremental_lookback('measured_hour_start_ts_utc', 'hour_start_ts_utc') }}

),

stations as (

    select * from {{ ref('dim_station') }}

),

final as (

    select
        measurements.station_id,
        measurements.measured_hour_start_ts_utc as hour_start_ts_utc,
        measurements.pollutant,
        -- SCD2 lookup: the station version valid at the measured hour
        stations.station_key,
        coalesce(stations.district_key, '-1') as district_key,
        {{ to_date_key(prague_local_date('measurements.measured_hour_start_ts_utc')) }} as date_key,
        measurements.measured_hour_start_local as hour_start_local,
        measurements.value_ugm3,
        measurements.averaging_hours,
        measurements.aq_hourly_index,
        measurements.published_ts_utc
    from measurements
    left join stations
        on
            measurements.station_id = stations.station_id
            and measurements.measured_hour_start_ts_utc >= stations.valid_from_ts_utc
            and measurements.measured_hour_start_ts_utc < stations.valid_to_ts_utc

)

select * from final
