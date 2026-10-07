-- SCD2 station dimension: one row per station version from the snapshot.
-- * The snapshot starts at its first run, so the first version of each station is valid from the
--   beginning of time; otherwise measurements older than the first snapshot would find no version.
-- * Consecutive snapshot rows with identical attributes are merged (e.g. the extra rows created
--   when a column is added to the snapshot), and each version ends where the next one starts, so
--   the history has neither duplicates nor gaps.

with snapshot as (

    select * from {{ ref('snap_golemio__air_quality_stations') }}

),

districts as (

    select
        district_key,
        district_slug
    from {{ ref('dim_district') }}

),

snapshot_rows as (

    select
        dbt_scd_id,
        station_id,
        station_name,
        district_slug,
        latitude,
        longitude,
        -- rows written before `is_listed` existed have null: the station was listed then
        coalesce(is_listed, true) as is_listed,
        coalesce(is_inferred, false) as is_inferred,
        cast(dbt_valid_from as timestamp with time zone) as dbt_valid_from
    from snapshot

),

changes as (

    select
        *,
        {{ dbt_utils.generate_surrogate_key(
            ['station_name', 'district_slug', 'latitude', 'longitude', 'is_listed', 'is_inferred']
        ) }} as attributes_hash
    from snapshot_rows

),

flagged as (

    select
        *,
        coalesce(
            attributes_hash
            != lag(attributes_hash) over (partition by station_id order by dbt_valid_from),
            true
        ) as starts_version
    from changes

),

islands as (

    select
        *,
        sum(cast(starts_version as integer)) over (
            partition by station_id order by dbt_valid_from
            rows between unbounded preceding and current row
        ) as version_number
    from flagged

),

versions as (

    select
        station_id,
        version_number,
        min(dbt_valid_from) as version_start
    from islands
    group by station_id, version_number

),

version_rows as (

    -- attributes and key of the first snapshot row of each version
    select
        islands.dbt_scd_id as station_key,
        islands.station_id,
        islands.version_number,
        islands.station_name,
        islands.district_slug,
        islands.latitude,
        islands.longitude,
        islands.is_listed,
        islands.is_inferred,
        versions.version_start
    from islands
    inner join versions
        on
            islands.station_id = versions.station_id
            and islands.version_number = versions.version_number
            and islands.dbt_valid_from = versions.version_start

),

final as (

    select
        version_rows.station_key,
        version_rows.station_id,
        version_rows.station_name,
        coalesce(districts.district_key, '-1') as district_key,
        version_rows.district_slug,
        version_rows.latitude,
        version_rows.longitude,
        version_rows.is_listed,
        version_rows.is_inferred,
        case
            when version_rows.version_number = 1
                then cast('1900-01-01' as timestamp with time zone)
            else version_rows.version_start
        end as valid_from_ts_utc,
        coalesce(
            lead(version_rows.version_start) over (
                partition by version_rows.station_id order by version_rows.version_number
            ),
            cast('9999-12-31' as timestamp with time zone)
        ) as valid_to_ts_utc,
        version_rows.version_number = max(version_rows.version_number) over (
            partition by version_rows.station_id
        ) as is_current
    from version_rows
    left join districts on version_rows.district_slug = districts.district_slug

)

select * from final
