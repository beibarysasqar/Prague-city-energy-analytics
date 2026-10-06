{#-
    Incremental filter with a lookback window for late-arriving data: on incremental runs only rows
    whose `source_column` is within `days` of the latest `target_column` already in the model are
    selected; `delete+insert` on the unique key then replaces them.
-#}
{% macro incremental_lookback(source_column, target_column=none, days=none) -%}
    {%- if is_incremental() -%}
        {%- set days = days or var('incremental_lookback_days', 3) -%}
        where {{ source_column }} >= (
            select max({{ target_column or source_column }}) from {{ this }}
        ) - interval '{{ days }} days'
    {%- endif -%}
{%- endmacro %}
