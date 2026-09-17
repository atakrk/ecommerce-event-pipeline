# transform/ (dbt)

The dbt project that builds the `staging` and `marts` layers in `data/warehouse.duckdb` from the loader's `raw` and `meta` tables. Governed by [spec 0001](../docs/specs/0001-pipeline-stack-architecture/index.md) (see *Architecture* for the invariants every model keeps).

## Files

- `dbt_project.yml`: project `ecommerce_event_pipeline`, pinned to dbt 1.12.x
- `profiles.yml` (committed, no secrets): DuckDB path from `DBT_DUCKDB_PATH`, falling back to `data/warehouse.duckdb` relative to the working directory; session `TimeZone` is `UTC`
- `macros/generate_schema_name.sql`: a model's custom schema is used as is (`staging`, `marts`), not dbt's default `main_staging`
- `packages.yml` + `package-lock.yml` (committed): `dbt_utils`; `dbt_packages/`, `target/`, `logs/` are gitignored
- `models/staging/`, `models/marts/`, `tests/`: empty until feature 1 adds the first models

## Commands

```bash
make dbt-build    # dbt deps + dbt build; Make exports DBT_DUCKDB_PATH as an absolute path
uv run dbt <cmd> --project-dir transform --profiles-dir transform   # direct call, from the repo root
```

## Constraints

- Always pass `--project-dir transform --profiles-dir transform` (Make does). Running dbt from another folder without `DBT_DUCKDB_PATH` creates a second, empty warehouse.
- DuckDB allows one writing process: close the DuckDB MCP server or DuckDB UI before `make dbt-build` or `make check`.
- `tests/test_dbt_project.py` runs real dbt against a temp copy of `dbt_project.yml`, `profiles.yml` and `macros/`; keep it passing when you change the profile or the macro.

_Drafted by /sync from the introducing change, worth a quick human pass._
