{#- True when the GeoJSON polygon contains the GeoJSON point (both stored as JSON strings). -#}
{% macro st_contains_geojson(polygon_geojson, point_geojson) -%}
    {{ return(adapter.dispatch('st_contains_geojson', 'prague')(polygon_geojson, point_geojson)) }}
{%- endmacro %}

{% macro duckdb__st_contains_geojson(polygon_geojson, point_geojson) -%}
    st_contains(st_geomfromgeojson({{ polygon_geojson }}), st_geomfromgeojson({{ point_geojson }}))
{%- endmacro %}

{% macro snowflake__st_contains_geojson(polygon_geojson, point_geojson) -%}
    st_contains(to_geography({{ polygon_geojson }}), to_geography({{ point_geojson }}))
{%- endmacro %}
