{#
  Notion select values often carry a leading emoji ("🟢 Done", "⚡ High").
  Strip the leading non-alphanumeric run so marts can group on clean labels.
  Leaves emoji-free values untouched ("Applied", "Recruiter Screen").
#}
{% macro strip_emoji(column) -%}
    nullif(trim(regexp_replace({{ column }}, '^[^A-Za-z0-9]+', '')), '')
{%- endmacro %}
