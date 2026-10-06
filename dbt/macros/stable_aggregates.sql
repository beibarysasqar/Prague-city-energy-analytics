{#-
    Deterministic float aggregates. Parallel aggregation adds DOUBLE values in a varying order, so
    results can differ in the last bit between runs; incremental models would then never match a full
    refresh exactly (and rounding only moves the problem to rounding boundaries). Aggregating in
    DECIMAL is exact, hence order-independent. `filter` is an optional SQL predicate.
-#}
{% macro stable_avg(expr, filter=none, scale=6) -%}
    cast(avg(cast({{ expr }} as decimal(18, {{ scale }})))
    {%- if filter %} filter (where {{ filter }}){% endif %} as double)
{%- endmacro %}

{% macro stable_sum(expr, filter=none, scale=6) -%}
    cast(sum(cast({{ expr }} as decimal(18, {{ scale }})))
    {%- if filter %} filter (where {{ filter }}){% endif %} as double)
{%- endmacro %}
