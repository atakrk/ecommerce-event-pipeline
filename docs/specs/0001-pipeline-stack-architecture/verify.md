# Verify: Stack and architecture · spec 0001 · updated 2026-09-17
_Spec 0001 is decision only and has no `AC-N` ids, so these steps come from the scope feature 0 **Done when** line, split into DW-1 to DW-4. `/check verify` runs these; `/test` locks the ones worth keeping._

## Commands
- [x] Remove `.venv`, then `uv sync --locked` → it creates `.venv` with Python 3.12 (`uv run python --version` prints `3.12.x`) and changes nothing in `uv.lock` → DW-1
- [x] `uv run python -c "import duckdb, dbt.version; print(duckdb.__version__, dbt.version.__version__)"` → versions are inside the `pyproject.toml` pins (`duckdb~=1.5.5`, `dbt-core~=1.12.5`) → DW-1
- [x] On `main` before this change (Python 3.9, `requirements.txt`) and on this branch, generate with the default `config.yaml` into two separate data folders (`reference`, then `generator.run --sessions 5000`, then `verify.py`) → `users.jsonl`, `products.jsonl`, `events.jsonl` are byte identical (`cmp` is silent) and `verify.py` prints the same report → DW-2
- [x] Run `make events` twice with the same seed → both `data/events.jsonl` files have the same SHA 256 → DW-2
- [x] `uv run pytest tests/test_determinism.py` → the golden hashes (recorded on Python 3.9) match → DW-2
- [x] `make lint` → `ruff check` and `ruff format --check` both pass → DW-3
- [x] `make test` → every pytest test passes → DW-3
- [x] `uv run pre-commit run --all-files` → both Ruff hooks pass → DW-3
- [ ] Push the branch or open a PR → the `CI` workflow in GitHub Actions runs `make check` and goes green → DW-3
- [x] Close anything holding `data/warehouse.duckdb`, then `make dbt-build` → `dbt deps` installs `dbt_utils` at the locked version, `dbt build` exits 0 with only the expected "Nothing to do" warning → DW-4
- [x] `DBT_DUCKDB_PATH="$PWD/data/warehouse.duckdb" uv run dbt debug --project-dir transform --profiles-dir transform` → connection OK and `path` is the absolute `data/warehouse.duckdb`, not a second file elsewhere → DW-4
- [x] `make check` → `data/*.jsonl` are unchanged afterwards (the seeded dataset goes to a temp folder) → DW-3, DW-4

## Acceptance criteria coverage
- DW-1 (`uv sync` sets up Python 3.12 with locked dependencies): steps 1 and 2
- DW-2 (`make reference`, `make events`, `make verify` output byte identical for the same seed): steps 3, 4, 5
- DW-3 (Ruff and pytest pass locally and in GitHub Actions): steps 6, 7, 8, 9, 12
- DW-4 (`dbt build` runs cleanly against `data/warehouse.duckdb` from an empty `transform/` project): steps 10, 11, 12
