{#- Fails for rows where the column is negative (nulls are allowed). Do not use on prices. -#}
{% test non_negative(model, column_name) %}

select *
from {{ model }}
where {{ column_name }} < 0

{% endtest %}
