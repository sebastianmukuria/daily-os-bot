{#
  Use the model's +schema as the literal schema name (STAGING, MARTS) instead of
  dbt's default of prefixing the target schema. Keeps a clean RAW/STAGING/MARTS
  layout in the warehouse.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
