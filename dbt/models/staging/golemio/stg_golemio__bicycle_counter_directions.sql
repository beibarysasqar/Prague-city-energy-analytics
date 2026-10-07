with source as (

    select * from {{ source('golemio', 'golemio_bicycle_counters') }}

),

-- Latest version of every record ever listed. Records the source removes from its list stay in the
-- reference with is_listed = false, so their history (measurements, detections) keeps a parent.
latest_version as (

    select
        *,
        load_date = max(load_date) over () as is_listed
    from source
    qualify row_number() over (partition by id order by load_date desc) = 1

),

directions as (

    select
        id as counter_id,
        unnest(json_extract(directions, '$[*]')) as direction
    from latest_version

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
