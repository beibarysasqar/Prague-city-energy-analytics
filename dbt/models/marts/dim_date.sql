with spine as (

    {{ dbt_utils.date_spine(
        datepart='day',
        start_date="cast('2025-01-01' as date)",
        end_date="cast('2028-01-01' as date)"
    ) }}

),

holidays as (

    select * from {{ ref('cz_public_holidays') }}

),

days as (

    select cast(date_day as date) as date_day
    from spine

),

final as (

    select
        {{ to_date_key('days.date_day') }} as date_key,
        days.date_day,
        cast(extract(year from days.date_day) as integer) as calendar_year,
        cast(extract(quarter from days.date_day) as integer) as calendar_quarter,
        cast(extract(month from days.date_day) as integer) as calendar_month,
        cast(extract(day from days.date_day) as integer) as day_of_month,
        cast(isodow(days.date_day) as integer) as iso_day_of_week,
        dayname(days.date_day) as day_name,
        cast(weekofyear(days.date_day) as integer) as iso_week,
        cast(isoyear(days.date_day) as integer) as iso_year,
        isodow(days.date_day) in (6, 7) as is_weekend,
        holidays.holiday_date is not null as is_cz_public_holiday,
        holidays.holiday_name,
        isodow(days.date_day) not in (6, 7) and holidays.holiday_date is null as is_working_day
    from days
    left join holidays on days.date_day = holidays.holiday_date

)

select * from final
