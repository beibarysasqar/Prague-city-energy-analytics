{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key=['counter_id', 'direction_id', 'local_date'],
    on_schema_change='append_new_columns'
) }}

-- Daily bicycle/pedestrian counts per counter direction and Prague calendar day.
-- A day has 288 five-minute slots (276 / 300 on DST switch days); `is_complete_day` marks days
-- with every slot present, so partial days (today, outages) can be excluded from trends.

with detections as (

    select
        *,
        {{ prague_local_date('interval_start_ts_utc') }} as local_date
    from {{ ref('stg_golemio__bicycle_detections') }}
    {{ incremental_lookback(prague_local_date('interval_start_ts_utc'), 'local_date') }}

),

daily as (

    select
        counter_id,
        direction_id,
        local_date,
        sum(bike_count) as bike_count,
        sum(pedestrian_count) as pedestrian_count,
        count(*) as n_slots,
        count(bike_count) as n_slots_with_bike_data
    from detections
    group by counter_id, direction_id, local_date

),

catalogue_directions as (

    select * from {{ ref('stg_golemio__bicycle_counter_directions') }}

),

-- Fallback names by direction id (camea-BC_PN-VYBR has a null direction in the catalogue but
-- shares direction ids with its sibling road counter).
direction_names as (

    select
        direction_id,
        min(direction_name) as direction_name
    from catalogue_directions
    group by direction_id

),

counters as (

    select
        counter_key,
        counter_id,
        district_key
    from {{ ref('dim_bike_counter') }}

),

final as (

    select
        daily.counter_id,
        daily.direction_id,
        daily.local_date,
        counters.counter_key,
        coalesce(counters.district_key, '-1') as district_key,
        {{ to_date_key('daily.local_date') }} as date_key,
        coalesce(
            catalogue_directions.direction_name, direction_names.direction_name
        ) as direction_name,
        catalogue_directions.direction_id is null as is_direction_inferred,
        daily.bike_count,
        daily.pedestrian_count,
        daily.n_slots,
        daily.n_slots_with_bike_data,
        cast(
            {{ dbt.datediff(
                "timezone('Europe/Prague', cast(daily.local_date as timestamp))",
                "timezone('Europe/Prague', cast(daily.local_date + 1 as timestamp))",
                'minute'
            ) }} / 5 as integer
        ) as expected_slots
    from daily
    left join counters on daily.counter_id = counters.counter_id
    left join catalogue_directions
        on
            daily.counter_id = catalogue_directions.counter_id
            and daily.direction_id = catalogue_directions.direction_id
    left join direction_names on daily.direction_id = direction_names.direction_id

)

select
    *,
    n_slots >= expected_slots as is_complete_day
from final
