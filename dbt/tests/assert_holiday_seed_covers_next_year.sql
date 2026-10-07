{{ config(severity='warn') }}

-- dim_date flags Czech public holidays from the seed; warn a year before the seed runs out so it
-- can be extended in time (otherwise new dates silently get is_cz_public_holiday = false).

select max(holiday_date) as last_holiday_in_seed
from {{ ref('cz_public_holidays') }}
having max(holiday_date) < current_date + interval '365 days'
