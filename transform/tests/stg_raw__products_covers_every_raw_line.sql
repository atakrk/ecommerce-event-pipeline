{{ config(severity = 'warn') }}
{{ raw_lines_missing_from_staging('products', ref('stg_raw__products')) }}
