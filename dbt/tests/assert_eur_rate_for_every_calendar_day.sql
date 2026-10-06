-- Energy prices are converted EUR -> CZK, so every calendar day needs an EUR rate.

with days as (

    select distinct calendar_date
    from {{ ref('stg_cnb__fx_rates_daily') }}

),

eur as (

    select calendar_date
    from {{ ref('stg_cnb__fx_rates_daily') }}
    where currency_code = 'EUR' and rate_czk_per_unit is not null

)

select days.calendar_date
from days
left join eur on days.calendar_date = eur.calendar_date
where eur.calendar_date is null
