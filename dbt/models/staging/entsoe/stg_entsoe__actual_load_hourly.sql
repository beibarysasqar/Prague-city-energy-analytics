-- 15-minute actual total load averaged to UTC hours (MW).

with source as (

    select * from {{ source('entsoe', 'entsoe_actual_load') }}

),

quarter_hours as (

    select
        cast(ts_utc as timestamp with time zone) as interval_start_ts_utc,
        cast(value as double) as load_mw,
        _loaded_at as loaded_at_ts_utc
    from source
    qualify row_number() over (partition by ts_utc order by _loaded_at desc) = 1

),

hourly as (

    select
        {{ dbt.date_trunc('hour', 'interval_start_ts_utc') }} as hour_start_ts_utc,
        {{ stable_avg('load_mw') }} as load_mw,
        count(*) as n_intervals,
        max(loaded_at_ts_utc) as loaded_at_ts_utc
    from quarter_hours
    group by 1

),

final as (

    select
        hour_start_ts_utc,
        timezone('Europe/Prague', hour_start_ts_utc) as hour_start_local,
        load_mw,
        n_intervals,
        loaded_at_ts_utc
    from hourly

)

select * from final
