-- One row per calendar day x currency. The source already repeats the last business day's
-- rates on weekends/holidays; any calendar day missing from bronze is forward-filled here.

with source as (

    select * from {{ source('cnb', 'cnb_fx_daily') }}

),

rates as (

    select
        load_date as calendar_date,
        currencycode as currency_code,
        cast(validfor as date) as rate_date,
        cast(rate as double) / cast(amount as double) as rate_czk_per_unit,
        cast(amount as integer) as quoted_amount,
        _loaded_at as loaded_at_ts_utc
    from source
    qualify row_number() over (
        partition by load_date, currencycode order by _loaded_at desc
    ) = 1

),

calendar as (

    {#- date_spine renders its own CTEs, so the bounds read the source relation, not `rates`. #}
    {{ dbt_utils.date_spine(
        datepart='day',
        start_date="(select min(load_date) from " ~ source('cnb', 'cnb_fx_daily') ~ ")",
        end_date="(select max(load_date) + interval '1 day' from "
            ~ source('cnb', 'cnb_fx_daily') ~ ")"
    ) }}

),

currencies as (

    select distinct currency_code from rates

),

grid as (

    select
        cast(calendar.date_day as date) as calendar_date,
        currencies.currency_code
    from calendar
    cross join currencies

),

filled as (

    select
        grid.calendar_date,
        grid.currency_code,
        rates.calendar_date is null as is_forward_filled,
        last_value(rates.rate_date ignore nulls) over w as rate_date,
        last_value(rates.rate_czk_per_unit ignore nulls) over w as rate_czk_per_unit,
        last_value(rates.quoted_amount ignore nulls) over w as quoted_amount,
        last_value(rates.loaded_at_ts_utc ignore nulls) over w as loaded_at_ts_utc
    from grid
    left join rates
        on
            grid.calendar_date = rates.calendar_date
            and grid.currency_code = rates.currency_code
    window w as (
        partition by grid.currency_code
        order by grid.calendar_date
        rows between unbounded preceding and current row
    )

)

select * from filled
