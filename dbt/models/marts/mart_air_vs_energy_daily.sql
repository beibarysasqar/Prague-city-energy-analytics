-- District x Prague calendar day: air pollution, weather and the (city-wide) electricity price,
-- ready for correlation analysis. The source switched from 3-hour running averages to 1-hour
-- values on 2026-08-12; daily means of both are comparable, `aq_averaging_hours` tells which
-- one a day is based on. stable_avg/stable_sum keep results deterministic.

with air_quality as (

    select
        district_key,
        date_key,
        {{ stable_avg('value_ugm3', filter="pollutant = 'PM2_5'") }} as avg_pm2_5_ugm3,
        {{ stable_avg('value_ugm3', filter="pollutant = 'PM10'") }} as avg_pm10_ugm3,
        {{ stable_avg('value_ugm3', filter="pollutant = 'NO2'") }} as avg_no2_ugm3,
        count(distinct station_id) as n_aq_stations,
        max(averaging_hours) as aq_averaging_hours
    from {{ ref('fact_air_quality_hourly') }}
    group by district_key, date_key

),

station_weather_daily as (

    select
        station_id,
        district_key,
        date_key,
        {{ stable_avg('temperature_c') }} as avg_temperature_c,
        {{ stable_avg('wind_speed_ms') }} as avg_wind_speed_ms,
        {{ stable_avg('relative_humidity_pct') }} as avg_relative_humidity_pct,
        {{ stable_sum('precipitation_mm') }} as precipitation_mm
    from {{ ref('fact_weather_hourly') }}
    group by station_id, district_key, date_key

),

weather as (

    select
        district_key,
        date_key,
        {{ stable_avg('avg_temperature_c') }} as avg_temperature_c,
        {{ stable_avg('avg_wind_speed_ms') }} as avg_wind_speed_ms,
        {{ stable_avg('avg_relative_humidity_pct') }} as avg_relative_humidity_pct,
        {{ stable_avg('precipitation_mm') }} as precipitation_mm
    from station_weather_daily
    group by district_key, date_key

),

energy as (

    select
        date_key,
        {{ stable_avg('price_eur_mwh') }} as avg_price_eur_mwh,
        {{ stable_avg('price_czk_mwh') }} as avg_price_czk_mwh,
        min(price_eur_mwh) as min_price_eur_mwh,
        max(price_eur_mwh) as max_price_eur_mwh,
        {{ stable_avg('load_mw') }} as avg_load_mw,
        count(*) as n_price_hours
    from {{ ref('fact_energy_price_hourly') }}
    group by date_key

),

districts as (

    select
        district_key,
        district_slug,
        district_name
    from {{ ref('dim_district') }}

),

dates as (

    select
        date_key,
        date_day,
        is_weekend,
        is_cz_public_holiday
    from {{ ref('dim_date') }}

),

final as (

    select
        air_quality.district_key,
        air_quality.date_key,
        districts.district_slug,
        districts.district_name,
        dates.date_day,
        dates.is_weekend,
        dates.is_cz_public_holiday,
        air_quality.avg_pm2_5_ugm3,
        air_quality.avg_pm10_ugm3,
        air_quality.avg_no2_ugm3,
        air_quality.n_aq_stations,
        air_quality.aq_averaging_hours,
        weather.avg_temperature_c,
        weather.avg_wind_speed_ms,
        weather.avg_relative_humidity_pct,
        weather.precipitation_mm,
        energy.avg_price_eur_mwh,
        energy.avg_price_czk_mwh,
        energy.min_price_eur_mwh,
        energy.max_price_eur_mwh,
        energy.avg_load_mw,
        energy.n_price_hours
    from air_quality
    left join weather
        on
            air_quality.district_key = weather.district_key
            and air_quality.date_key = weather.date_key
    left join energy on air_quality.date_key = energy.date_key
    left join districts on air_quality.district_key = districts.district_key
    left join dates on air_quality.date_key = dates.date_key

)

select * from final
