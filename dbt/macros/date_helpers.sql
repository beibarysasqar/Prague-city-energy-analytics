{#- Integer date key YYYYMMDD of a DATE expression (joins to dim_date.date_key). -#}
{% macro to_date_key(date_expr) -%}
    cast(
        extract(year from {{ date_expr }}) * 10000
        + extract(month from {{ date_expr }}) * 100
        + extract(day from {{ date_expr }})
        as integer
    )
{%- endmacro %}

{#- Calendar date in Europe/Prague of a UTC TIMESTAMPTZ expression. -#}
{% macro prague_local_date(ts_utc_expr) -%}
    {{ return(adapter.dispatch('prague_local_date', 'prague')(ts_utc_expr)) }}
{%- endmacro %}

{% macro duckdb__prague_local_date(ts_utc_expr) -%}
    cast(timezone('Europe/Prague', {{ ts_utc_expr }}) as date)
{%- endmacro %}

{% macro snowflake__prague_local_date(ts_utc_expr) -%}
    cast(convert_timezone('Europe/Prague', {{ ts_utc_expr }}) as date)
{%- endmacro %}
