{#-
    Fails for rows where the column is negative (nulls are allowed). Do not use on prices.
    Checks source measurements, so it warns by default (override with config severity).
-#}
{% test non_negative(model, column_name) %}

{{ config(severity='warn') }}


select *
from {{ model }}
where {{ column_name }} < 0

{% endtest %}
