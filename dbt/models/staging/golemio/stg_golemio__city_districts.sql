with source as (

    select * from {{ source('golemio', 'golemio_city_districts') }}

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
        cast(id as integer) as district_id,
        name as district_name,
        slug as district_slug,
        geometry as geometry_geojson,
        cast(updated_at as timestamp with time zone) as source_updated_ts_utc,
        first_seen_date,
        load_date as last_listed_date,
        is_listed,
        _loaded_at as loaded_at_ts_utc
    from latest_version

)

select * from renamed
