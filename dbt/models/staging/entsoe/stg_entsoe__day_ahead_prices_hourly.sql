-- 15-minute day-ahead prices (MTU 15 min since Oct 2025) averaged to UTC hours.

with source as (

    select * from {{ source('entsoe', 'entsoe_day_ahead_prices') }}

),

quarter_hours as (

    select
        cast(ts_utc as timestamp with time zone) as interval_start_ts_utc,
        cast(value as double) as price_eur_mwh,
        _loaded_at as loaded_at_ts_utc
    from source
    qualify row_number() over (partition by ts_utc order by _loaded_at desc) = 1

),

hourly as (

    select
        {{ dbt.date_trunc('hour', 'interval_start_ts_utc') }} as hour_start_ts_utc,
        avg(price_eur_mwh) as price_eur_mwh,
        min(price_eur_mwh) as min_price_eur_mwh,
        max(price_eur_mwh) as max_price_eur_mwh,
        count(*) as n_intervals,
        max(loaded_at_ts_utc) as loaded_at_ts_utc
    from quarter_hours
    group by 1

),

final as (

    select
        hour_start_ts_utc,
        timezone('Europe/Prague', hour_start_ts_utc) as hour_start_local,
        price_eur_mwh,
        min_price_eur_mwh,
        max_price_eur_mwh,
        n_intervals,
        loaded_at_ts_utc
    from hourly

)

select * from final
