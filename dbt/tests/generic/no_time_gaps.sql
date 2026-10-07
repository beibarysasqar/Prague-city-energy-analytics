{#-
    Fails for every gap in a regular time series: rows where the next distinct timestamp of the same
    partition (e.g. per station) is more than one `datepart` later. Apply on UTC timestamps so DST
    switches do not create false gaps.

    Gaps come from the source, so it warns by default (override with config severity).
-#}
{% test no_time_gaps(model, column_name, partition_by=[], datepart='hour') %}

{{ config(severity='warn') }}


{%- set partition_cols = partition_by | join(', ') %}

with points as (

    select distinct
        {% if partition_by %}{{ partition_cols }},{% endif %}
        {{ column_name }} as ts
    from {{ model }}
    where {{ column_name }} is not null

),

ordered as (

    select
        *,
        lead(ts) over (
            {% if partition_by %}partition by {{ partition_cols }}{% endif %}
            order by ts
        ) as next_ts
    from points

)

select *
from ordered
where next_ts > {{ dbt.dateadd(datepart, 1, 'ts') }}

{% endtest %}
