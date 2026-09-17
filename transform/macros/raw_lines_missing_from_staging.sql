{#
    Rows where a load's raw lines did not all reach its staging model.

    Staging is one to one with the raw lines that parsed, so a gap here has exactly
    one meaning: a line that is not valid JSON, or a present value that would not
    cast. These tests warn rather than fail on purpose. Feature 2 replaces them with
    real quarantine models that can say which line and why.
#}
{% macro raw_lines_missing_from_staging(source_table, staging_model) %}

with raw_lines as (

    select load_id, count(*) as raw_count
    from {{ source('raw', source_table) }}
    group by load_id

),

staged_lines as (

    select load_id, count(*) as staged_count
    from {{ staging_model }}
    group by load_id

)

select
    raw_lines.load_id,
    raw_lines.raw_count,
    coalesce(staged_lines.staged_count, 0) as staged_count

from raw_lines
left join staged_lines using (load_id)
where raw_lines.raw_count <> coalesce(staged_lines.staged_count, 0)

{% endmacro %}
