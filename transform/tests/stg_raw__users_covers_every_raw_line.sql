{{ config(severity = 'warn') }}
{{ raw_lines_missing_from_staging('users', ref('stg_raw__users')) }}
