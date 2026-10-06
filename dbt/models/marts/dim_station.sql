-- SCD2 station dimension: one row per station version from the snapshot. The snapshot starts at
-- its first run, so the first version of each station is made valid from the beginning of time;
-- otherwise measurements older than the first snapshot would find no version.

with snapshot as (

    select * from {{ ref('snap_golemio__air_quality_stations') }}

),

districts as (

    select
        district_key,
        district_slug
    from {{ ref('dim_district') }}

),

versions as (

    select
        dbt_scd_id as station_key,
        station_id,
        station_name,
        district_slug,
        latitude,
        longitude,
        case
            when row_number() over (partition by station_id order by dbt_valid_from) = 1
                then cast('1900-01-01' as timestamp with time zone)
            else cast(dbt_valid_from as timestamp with time zone)
        end as valid_from_ts_utc,
        coalesce(
            cast(dbt_valid_to as timestamp with time zone),
            cast('9999-12-31' as timestamp with time zone)
        ) as valid_to_ts_utc,
        dbt_valid_to is null as is_current
    from snapshot

),

final as (

    select
        versions.station_key,
        versions.station_id,
        versions.station_name,
        coalesce(districts.district_key, '-1') as district_key,
        versions.district_slug,
        versions.latitude,
        versions.longitude,
        versions.valid_from_ts_utc,
        versions.valid_to_ts_utc,
        versions.is_current
    from versions
    left join districts on versions.district_slug = districts.district_slug

)

select * from final
