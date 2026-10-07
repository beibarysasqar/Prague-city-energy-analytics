with districts as (

    select * from {{ ref('stg_golemio__city_districts') }}

),

known as (

    select
        {{ dbt_utils.generate_surrogate_key(['district_slug']) }} as district_key,
        district_id,
        district_slug,
        district_name,
        geometry_geojson,
        is_listed
    from districts

),

-- Unknown member so facts with an unmapped district still join.
unknown as (

    select
        '-1' as district_key,
        cast(null as integer) as district_id,
        'unknown' as district_slug,
        'Unknown' as district_name,
        cast(null as varchar) as geometry_geojson,
        true as is_listed

)

select * from known
union all
select * from unknown
