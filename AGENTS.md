# ecommerce-event-pipeline

## Stack

Decided in [spec 0001](docs/specs/0001-pipeline-stack-architecture/index.md).

- **Language / Runtime**: Python 3.12, managed by uv (`pyproject.toml`, `uv.lock`)
- **Pattern**: local layered batch pipeline: raw → staging (+ quarantine) → marts, in one DuckDB file (`data/warehouse.duckdb`)
- **Key dependencies**: `duckdb`, dbt Core + `dbt-duckdb` + `dbt_utils` (project in `transform/`), PyYAML
- **Orchestration**: Make · **Tests**: pytest + dbt tests · **Lint/format**: Ruff (no type checker) · **CI**: GitHub Actions

## Build approach

**Skateboard**: ship the thinnest usable whole first, then grow it.

## Commands

```bash
uv sync                   # install Python 3.12 + locked dependencies (`make sync` = `uv sync --locked`)
uv run pre-commit install # once per clone: Ruff on every commit
make reference events     # generate data/users, products, events .jsonl
make verify               # structural checks + funnel report on events.jsonl
make pipeline             # after feature 1: load → dbt deps → dbt build → report
make lint / make test     # ruff check + format check / pytest
make dbt-build            # dbt deps + dbt build on data/warehouse.duckdb (close other DuckDB clients first)
make check                # lint + test + seeded generate and verify in a temp dir + dbt build (what CI runs)
make clean-warehouse      # delete data/warehouse.duckdb and all load history
```

## Specs

Stored in `docs/specs/`. Format: `docs/specs/NNNN-title.md`, or `NNNN-title/index.md` + `rationale.md`. Scope lives in `docs/scope/scope.md`.

## Rules

- **Functional style**: plain module functions and immutable data (tuples, namedtuples, dicts built once). No classes where a function works; module level names are constants only.
- Keep functions pure; push side effects (files, stdout, DuckDB writes) to `main()` or a thin I/O edge, so logic tests need no mocks.
- Folders by pipeline stage: `reference/`, `generator/`, `pipeline/` (load, report), `transform/` (dbt); shared helpers go in `common.py`, never copied.
- Every script opens with a module docstring saying what it does and how to run it.
- Data goes to stdout, diagnostics to stderr. Validate config and CLI args first and raise `ConfigError` naming the key and value, before any output.
- Determinism is load bearing: seeded RNGs via `rng_for` (`"<seed>:<name>"`), simulated time never wall clock, same seed gives byte identical output.
- `tests/test_determinism.py` pins generator output with golden SHA 256 hashes; change them only for a deliberate, explained output change.
- Files are written atomically (`.tmp` then `os.replace`); timestamps are ISO 8601 UTC with `Z` (`INSTANT_FORMAT`).
- dbt: dbt Labs naming (`stg_<source>__<entity>`, `int_`, `fct_`, `dim_`, `_quarantine`); lowercase SQL; import CTEs from `ref()`/`source()` then a final select; raw source tests `severity: warn`, staging and mart tests `severity: error`; no snapshots.
- Tests ship with every change: pytest for Python, dbt tests for models, and the funnel mart must match `verify.py` on the same seeded data.
- Tooling: Ruff lint + format as a `pre-commit` hook; pytest and dbt checks run in `make check` and CI. Commit messages are plain imperative subjects ("Add ...", "Move ...").

## Git

- integration: on
- branch prefix: feat/
- commit: per-milestone

## Agent skills

- [using-dbt-for-analytics-engineering](.claude/skills/using-dbt-for-analytics-engineering/): `dbt-labs/dbt-agent-skills`, building and changing dbt models, data tests, debugging
- [running-dbt-commands](.claude/skills/running-dbt-commands/): `dbt-labs/dbt-agent-skills`, formatting and running dbt CLI commands
- [adding-dbt-unit-test](.claude/skills/adding-dbt-unit-test/): `dbt-labs/dbt-agent-skills`, dbt unit tests with mocked inputs
- [configuring-dbt-mcp-server](.claude/skills/configuring-dbt-mcp-server/): `dbt-labs/dbt-agent-skills`, setting up the dbt MCP server

Declined: Astral uv skill, Astral Ruff skill
MCP servers: duckdb (connected, read only on `data/warehouse.duckdb`), dbt (recommended, add after feature 0)

## Context files

<!-- Nested AGENTS.md files are listed here as they are created -->

- [transform/AGENTS.md](transform/AGENTS.md): dbt project layout, profile and schema naming, how to run dbt safely

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
