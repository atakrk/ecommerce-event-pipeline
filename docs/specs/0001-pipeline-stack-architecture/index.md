# 0001. Local layered batch pipeline on DuckDB and dbt

**Date**: 2026-09-17
**Status**: Accepted

## Summary

This decides the tools and the overall shape for everything after event generation: loading, validation, reporting, quality tracking, and anomaly detection. Data flows in batches through three layers inside one local DuckDB file (an analytics database that lives in a single file): raw copies of the input, cleaned and validated staging tables, and reporting tables called marts. A small Python loader fills the raw layer, dbt (a tool that builds tables from version controlled SQL files and tests them) builds the other two, and Make runs the steps in order. It all runs on your laptop with no servers, and it uses the same patterns a real warehouse team uses.

## Requirements

Decision only spec: no feature acceptance criteria. Each scope feature (1 to 6) writes its own in its own spec and builds on this stack. Settled here: the stack every one of those features must use, listed in `## Proposed stack`, and the invariants in *Architecture*.

## Decision

**Chosen option**: Option 1: Local warehouse (DuckDB + dbt + Python loader + Make)

Build the pipeline as a layered batch flow (raw, then staging with quarantine, then marts) inside one DuckDB file, loaded by a small Python loader, transformed and tested by dbt Core, reported by a Python CLI, run by Make, on Python 3.12 managed by uv.

**Implementation skills**: `using-dbt-for-analytics-engineering` (`dbt-labs/dbt-agent-skills`, `.claude/skills/using-dbt-for-analytics-engineering/`) · `running-dbt-commands` (`dbt-labs/dbt-agent-skills`, `.claude/skills/running-dbt-commands/`) · `adding-dbt-unit-test` (`dbt-labs/dbt-agent-skills`, `.claude/skills/adding-dbt-unit-test/`) · `configuring-dbt-mcp-server` (`dbt-labs/dbt-agent-skills`, `.claude/skills/configuring-dbt-mcp-server/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Proposed stack

| Layer | Choice | Reason |
|---|---|---|
| Pipeline pattern | Layered batch: raw → staging (+ quarantine) → marts | Every release in scope (validation, messiness, metrics per run, anomalies) needs an untouched raw copy to measure against (basis: medallion layering). |
| Language | Python 3.12 | 3.9 is past end of life and blocks current data libraries; 3.12 is supported by all of them. |
| Environment & dependencies | uv, `pyproject.toml`, `uv.lock`, `.python-version`; `duckdb`, `dbt-core`, `dbt-duckdb` pinned to a minor version | One tool installs Python, manages the virtual env, and locks versions, so runs match across your machine and CI. Pinning keeps the DuckDB file format stable for every tool that opens it. Replaces `requirements.txt` and the `make venv` target. |
| Store | DuckDB, single file `data/warehouse.duckdb` (gitignored) | Built for analytics, reads JSON natively, has window functions, and needs no server. |
| Extract & load | `pipeline/load.py`, Python + `duckdb` package, argparse + `common.py`; one bulk DuckDB read per file, never row by row inserts from Python | Keeps load separate from transform, matches the existing script style, and stays fast at a few hundred thousand lines. |
| Raw layer | Append only, one table per kind of input file (`raw.<kind>`), one row per input line stored as text with its exact line number, keyed by `load_id` | Nothing is ever lost, malformed lines are still measurable, arrival order is preserved, and every run has a key (basis: immutable raw layer). |
| Run identity | One `make pipeline` = one run = one integer `load_id` from a sequence | Integer ids order runs correctly for "last N runs" comparisons. |
| Run metadata | `meta.load_runs`, `meta.load_files`, `meta.run_config` written by the loader | Idempotent reloads (loading the same file twice changes nothing), plus the config and generation settings each run used. |
| Generation manifest | Generator `--manifest PATH` writes `data/events.manifest.json` (seed, sessions, start, effective funnel and future mess rates); the loader stores it in `meta.run_config` | CLI overrides never reach `config.yaml`, and config can change between generating and loading, so the run must record what actually produced its data. |
| Transform | dbt Core + `dbt-duckdb` adapter, project in `transform/` | Models, tests, docs, and lineage out of the box; the most transferable skill for this learning project. |
| Materialization | Staging and marts rebuilt in full as tables on every run, covering all loads, every row carrying `load_id` | At this size a rebuild takes seconds, results always match raw, and history across runs stays queryable. Incremental models wait for the deferred incremental runs feature. |
| Record validation | SQL in staging: valid rows to `stg_*`, failing rows to `stg_*_quarantine` with a reason | Quarantine counts become plain queries, and Release 3 metrics come almost free. |
| Data tests | dbt built in generic tests + `dbt_utils`; tests on raw sources use `severity: warn`, tests on staging (after quarantine and dedup) and marts use `severity: error` | Covers the common checks without a heavy package, and deliberate Release 2 mess warns at the source instead of blocking every mart. |
| dbt snapshots | Not used | Snapshots keep state outside raw and break the rule that everything rebuilds from raw. Dimension history is derived in SQL from raw load order. |
| Config into SQL | Loader snapshots `config.yaml` values and the generation manifest into `meta.run_config`; dbt reads it as a source | `config.yaml` stays the single source of truth, and each run remembers the settings it used. |
| dbt profile | `transform/profiles.yml`, committed; path from `DBT_DUCKDB_PATH` (Make exports an absolute path), falling back to `data/warehouse.duckdb`; a `generate_schema_name` macro override | A local file needs no secrets. The absolute path stops dbt from creating a second, empty warehouse when run from another directory, and the macro keeps schemas named `staging` and `marts` instead of dbt's default `main_staging`. |
| dbt packages | `dbt_utils` in `transform/packages.yml`, `package-lock.yml` committed, `dbt deps` run by Make | A fresh clone and CI resolve the same package version. |
| Reporting | dbt marts + `pipeline/report.py` printing aligned tables; defaults to the latest events load, `--load-id N` picks another; prints the run's quarantine summary | Repeatable command whose output lines up with `verify.py`, and the one place a run reports quarantined counts. |
| Anomaly detection | SQL in dbt marts (stage rates vs configured rates with sampling noise) | No new dependency; flags are just another mart. The exact method belongs to the Release 4 spec. |
| Orchestration | Make (`make pipeline`: load → `dbt deps` → `dbt build` → report; `make clean-warehouse` deletes the warehouse on purpose) | Already in the repo; dbt orders the SQL, so a few sequential steps need nothing more. |
| Generator check | `verify.py` stays | It tests the generator's output and gives the golden numbers the funnel mart must match. |
| Python tests | pytest with small fixture JSONL files and a temporary DuckDB file | Covers the loader's idempotency and config parsing, and report formatting. |
| Lint & format | Ruff (lint + format), no type checker | The code has no type hints today, so a checker would add noise; revisit if hints appear. |
| CI | GitHub Actions: `uv sync`, Ruff, pytest, then a small seeded generate → load → `dbt build` | Catches breakage end to end on every push. |
| Observability | `meta.load_runs` row + stderr summary per load (load status); dbt's `target/run_results.json` (build status); quality metrics per run as a mart over all loads | Queryable run history with no logging service. |

## Architecture

**Flow**

```
reference/ + generator/  ──>  data/*.jsonl  +  data/events.manifest.json
                                   │
make pipeline                      ▼
  1. pipeline/load.py   ──>  raw.<kind>  +  meta.load_runs / load_files / run_config
  2. dbt deps + build   ──>  staging.stg_*  +  staging.stg_*_quarantine  ──>  marts.*   (+ dbt tests)
  3. pipeline/report.py ──>  stdout tables from marts.* for the latest events load
```

**Repository layout (target)**

```
common.py, config.yaml, verify.py        existing, kept
reference/, generator/                   existing, kept
pipeline/        load.py, report.py      new Python (argparse, uses common.py)
transform/       dbt_project.yml, profiles.yml, packages.yml,
                 models/staging/, models/marts/, tests/
tests/           pytest suite + fixtures/*.jsonl
pyproject.toml, uv.lock, .python-version
.github/workflows/ci.yml
```

**DuckDB schemas**

| Schema | Written by | Holds |
|---|---|---|
| `raw` | loader | one table per kind of input file (`raw.events`, `raw.users`, `raw.products` today; a new kind such as a Release 2 change file gets its own `raw.<kind>`): `load_id`, `source_file`, `line_number`, `raw_line` (the untouched text line) |
| `meta` | loader | `load_runs` (one row per run: integer id, start and end time, load status, row counts), `load_files` (file kind, path, SHA 256 content hash, `load_id`), `run_config` (config values and generation manifest per `load_id`) |
| `staging` | dbt | typed, validated rows carrying `load_id`; one quarantine model per source with a `quarantine_reason` column |
| `marts` | dbt | reporting tables carrying `load_id` (funnel, quality metrics per run, anomaly flags) |

**Loader input**: by default the files named under `config.paths` (`events.jsonl`, `users.jsonl`, `products.jsonl`, plus `events.manifest.json`); explicit paths may be passed instead. Each file is read in one bulk DuckDB read that keeps every line, in file order, with its exact line number (arrival order is how Release 2 out of order rows are measured). A file that is not valid UTF 8 aborts that load with a clear stderr error; problems inside a line are staging's job to quarantine.

**Invariants every feature must keep**

- Raw is append only and is never trimmed. Nothing downstream updates or deletes raw rows; any fix happens in staging. History lives only in the warehouse, so `make clean-data` still deletes only the JSONL files, and only `make clean-warehouse` removes history.
- A load is atomic: raw rows plus its `meta` rows commit in one transaction, or nothing does.
- A file whose content hash is already in `meta.load_files` is skipped with a stderr message, never loaded twice. Deduplication of individual records across loads happens in staging.
- Every staging and mart row carries the `load_id` it came from. Reports default to the latest events load.
- Reference data for an events load is the most recent loaded copy of each reference file whose `load_id` is at or before that events load's `load_id`.
- Staging and marts are fully derivable from raw plus `meta`. Deleting `data/warehouse.duckdb` and loading the same files again in the same order rebuilds the same state, apart from load timestamps.
- All timestamps are UTC. They are parsed to DuckDB `TIMESTAMP` in staging, and the dbt profile sets the session `TimeZone` to `UTC`.
- dbt reads the loader's tables only through `sources`, never by hard coded table names.
- Only one process may have the DuckDB file open while `make pipeline` runs. DuckDB allows one process that writes or several that only read, never both at once, so MCP servers and the DuckDB UI must close the file (or open a copy) during a run.
- Data output goes to stdout and diagnostics go to stderr, as in the existing scripts.

**Configuration**: no secrets. `config.yaml` stays the only project config. One environment variable, `DBT_DUCKDB_PATH`, which Make exports as the absolute warehouse path; dbt falls back to `data/warehouse.duckdb`. The CI run uses a small seeded dataset (for example `--sessions 2000`) so it stays fast and deterministic.

## Consequences

**Positive**:
- Every later feature has a fixed home: validation rules in staging models, metrics and anomalies in marts, run history in `meta`.
- Reruns are safe and reproducible: deterministic input, idempotent load, full rebuild.
- The SQL and dbt patterns carry straight over to Snowflake, BigQuery, or Postgres warehouses.

**Negative / tradeoffs**:
- Two languages and two test runners (pytest and dbt tests) to keep in your head, plus dbt's own concepts (`ref`, `source`, `profiles`, packages).
- DuckDB file locking: an MCP server or the DuckDB UI with the file open, even read only, makes the load or `dbt build` fail with a lock error. You close them before each run.
- A full rebuild reprocesses all raw history every run, and raw is never trimmed. That is fine at the current size; if it gets slow, the answer is the deferred incremental runs feature, not deleting history.
- Every model has to carry and join on `load_id`, and reference joins must pick the right copy per load. That is more SQL than a single snapshot design.
- Keeping raw as text means every type cast lives in staging SQL, which is more SQL to write than letting the loader infer types.
- The Python 3.12 and uv switch changes setup for anyone with the old `.venv`, and the `make venv` target goes away.
- Make gives no run UI, retries, or lineage view; dbt docs cover lineage only for the SQL layer.

**Neutral**:
- `requirements.txt` is replaced by `pyproject.toml` + `uv.lock`; Make targets call `uv run ...`.
- The Python 3.9 workaround for the `Z` suffix in `common.parse_instant` can be removed (3.11 and later parse it natively).
- `transform/target/`, `transform/dbt_packages/`, and `transform/logs/` must be gitignored; `transform/package-lock.yml` is committed.
- The generator gains a `--manifest PATH` option, and `make events` passes `data/events.manifest.json`.
- A new `make clean-warehouse` target; `make clean-data` keeps its current behavior.
- Scope build approach is Skateboard: the first feature (ingestion) should stand up the thinnest whole path (load → one staging model → one mart → report) before widening.

## Follow-up

- [ ] Migrate the environment before feature 1: move to Python 3.12, uv, and `pyproject.toml`; point Make at `uv run`; add Ruff, pytest, and CI; drop the `Z` suffix workaround. Tracked as scope feature 0, Stack and architecture.
- [ ] `AGENTS.md` does not exist yet. Run `/audit` to create it with this stack, and record under `## Agent skills` the four installed dbt skills (`.claude/skills/`), the DuckDB and dbt MCP servers once connected, and `Declined:` the Astral uv and Ruff skills.
- [ ] Connect the MCP servers you picked, in your own MCP settings: MotherDuck's DuckDB server (`motherduckdb/mcp-server-motherduck`, pointed at `data/warehouse.duckdb` in read only mode, on a DuckDB version compatible with the pinned `duckdb`) and dbt Labs' `dbt-mcp` (the installed `configuring-dbt-mcp-server` skill walks through it). Close both before `make pipeline`.
- [ ] Your Node is 20.10; the `npx skills` installer needs 20.12 or later. Upgrade Node if you want to install skills with the CLI later.
- [ ] Details still owned by each feature spec: the event dedup key and the exact quarantine rules (features 1 and 2), how late dimension changes are generated and stored (feature 4, within the no snapshots and `raw.<kind>` rules above), metric definitions (feature 5), and the anomaly method and thresholds (feature 6).
- [ ] Before adopting Dagster or Prefect for the deferred incremental runs feature, check the reported Prefect and Dagster merger (see References).
