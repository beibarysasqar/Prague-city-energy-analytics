with source as (

    select * from {{ source('golemio', 'golemio_bicycle_counters') }}

),

latest_snapshot as (

    select *
    from source
    qualify load_date = max(load_date) over ()

),

directions as (

    select
        id as counter_id,
        unnest(json_extract(directions, '$[*]')) as direction
    from latest_snapshot

),

renamed as (

    select
        counter_id,
        json_extract_string(direction, '$.id') as direction_id,
        json_extract_string(direction, '$.name') as direction_name
    from directions

),

-- The catalogue lists `{"id": null}` for camea-BC_PN-VYBR (cycle path) although it reports
-- detections for camea-PN-VY / camea-PN-BR; such placeholders are dropped here.
final as (

    select * from renamed
    where direction_id is not null

)

select * from final
