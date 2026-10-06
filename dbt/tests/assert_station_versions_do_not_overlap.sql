-- SCD2 sanity: versions of the same station must not overlap in time.

with versions as (

    select
        station_id,
        station_key,
        valid_from_ts_utc,
        valid_to_ts_utc
    from {{ ref('dim_station') }}

)

select
    a.station_id,
    a.station_key,
    b.station_key as overlapping_station_key
from versions as a
inner join versions as b
    on
        a.station_id = b.station_id
        and a.station_key < b.station_key
        and a.valid_from_ts_utc < b.valid_to_ts_utc
        and a.valid_to_ts_utc > b.valid_from_ts_utc
