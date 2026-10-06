{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='hour_start_ts_utc',
    on_schema_change='append_new_columns'
) }}

-- Day-ahead price and actual load per UTC hour. EUR -> CZK uses the ČNB rate of the hour's Prague
-- calendar date; prices for tomorrow (published the day before) fall back to the latest known rate
-- and are re-processed by the incremental lookback once the real rate exists.

with prices as (

    select *
    from {{ ref('stg_entsoe__day_ahead_prices_hourly') }}
    {{ incremental_lookback('hour_start_ts_utc') }}

),

load as (

    select * from {{ ref('stg_entsoe__actual_load_hourly') }}

),

eur_rates as (

    select
        calendar_date,
        rate_czk_per_unit as eur_czk_rate
    from {{ ref('stg_cnb__fx_rates_daily') }}
    where currency_code = 'EUR'

),

latest_eur_rate as (

    select eur_czk_rate
    from eur_rates
    qualify row_number() over (order by calendar_date desc) = 1

),

hourly as (

    select
        prices.hour_start_ts_utc,
        prices.hour_start_local,
        {{ prague_local_date('prices.hour_start_ts_utc') }} as local_date,
        prices.price_eur_mwh,
        prices.min_price_eur_mwh,
        prices.max_price_eur_mwh,
        prices.n_intervals as n_price_intervals,
        load.load_mw,
        load.n_intervals as n_load_intervals
    from prices
    left join load on prices.hour_start_ts_utc = load.hour_start_ts_utc

),

final as (

    select
        hourly.hour_start_ts_utc,
        {{ to_date_key('hourly.local_date') }} as date_key,
        hourly.hour_start_local,
        hourly.price_eur_mwh,
        hourly.min_price_eur_mwh,
        hourly.max_price_eur_mwh,
        coalesce(eur_rates.eur_czk_rate, latest_eur_rate.eur_czk_rate) as eur_czk_rate,
        eur_rates.eur_czk_rate is null as is_eur_rate_estimated,
        hourly.price_eur_mwh
        * coalesce(eur_rates.eur_czk_rate, latest_eur_rate.eur_czk_rate) as price_czk_mwh,
        hourly.load_mw,
        hourly.n_price_intervals,
        hourly.n_load_intervals
    from hourly
    left join eur_rates on hourly.local_date = eur_rates.calendar_date
    cross join latest_eur_rate

)

select * from final
