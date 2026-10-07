{{ config(severity='warn') }}

-- Facts reference stations/counters that are in none of the loaded snapshots (inferred members).
-- Expected for a while after the source removes a record (e.g. fresh CI loads); a warning makes it
-- visible without stopping the build.

select
    'station' as reference,
    station_id as member_id
from {{ ref('stg_golemio__air_quality_stations') }}
where is_inferred
union all
select
    'counter' as reference,
    counter_id as member_id
from {{ ref('stg_golemio__bicycle_counters') }}
where is_inferred
