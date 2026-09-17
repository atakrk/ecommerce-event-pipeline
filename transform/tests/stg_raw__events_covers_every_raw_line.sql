{{ config(severity = 'warn') }}
{{ raw_lines_missing_from_staging('events', ref('stg_raw__events')) }}
