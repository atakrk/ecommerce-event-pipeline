# 0001. Local layered batch pipeline on DuckDB and dbt: rationale

Decision record for [index.md](index.md). Builds do not need to read this file.

## Context

The repo generates deterministic synthetic ecommerce data today: reference tables (`reference/`), a funnel shaped event stream (`generator/`), and a structural checker (`verify.py`). It uses plain Python, PyYAML, argparse, JSONL files, and Make. Everything after generation is still unbuilt. Release 1 needs a queryable store with idempotent loads, validation with quarantine, and a funnel report that matches `verify.py`. Release 2 adds deliberate mess (duplicates, out of order arrival, late dimension changes). Release 3 tracks quality metrics per run. Release 4 flags anomalies. (Source: `docs/scope/scope.md`.)

The forces that shaped the choice:

- **Purpose is learning real data engineering.** Patterns and tools should transfer to production warehouse work, not just produce numbers.
- **One person, one laptop, no budget for operations.** Anything that needs a running server, Docker, or a hosted account is a cost paid on every session.
- **Small data.** 10k users, 1k products, thousands to a few hundred thousand events per run. Nothing here needs distributed compute.
- **Runs must be comparable over time.** Releases 3 and 4 compare runs, so each run needs a key, a record of its config, and the untouched input it saw.
- **Bad data must be kept, not dropped.** Quarantine rates are a Release 3 metric, and Release 2 injects bad data on purpose.
- **The runtime is aging.** The venv runs the macOS system Python 3.9.6, which stopped getting updates in October 2025 and is being dropped by data libraries.
- **Determinism is an existing design value** (README *Design decisions*): same seed, same output. The pipeline must not break that.

Without a decision, each Release 1 feature would pick its own storage and validation approach, and Release 3 would then have to retrofit run keys and history into all of them.

## Options considered

### Option 1: Local warehouse (DuckDB + dbt + Python loader + Make)

A Python loader appends raw lines to a DuckDB file with run metadata; dbt builds validated staging, quarantine, and marts with tests; a Python CLI prints reports; Make chains the steps; uv and Python 3.12 underneath.

**Pros**:
- Mirrors how analytics teams actually work (raw, staging, marts; SQL models with tests), with no servers.
- Run keys, config snapshots, and quarantine are first class from day one, so Releases 2 to 4 slot in.
- DuckDB handles this data size in memory with warehouse style SQL.

**Cons**:
- Learning dbt on top of Python; two test runners.
- Single writer lock on the DuckDB file.
- Full rebuilds do not teach incremental processing yet.

### Option 2: Code only, no framework (DuckDB + plain SQL runner + Pydantic)

Pydantic validates each JSONL line in Python and writes failures to a quarantine file; valid rows go to DuckDB; a small runner executes ordered `.sql` files for marts.

**Pros**:
- One language for validation, precise error messages, full control, no framework concepts.
- Fewer dependencies.

**Cons**:
- You build dependency ordering, SQL tests, and docs yourself, which is the part dbt already solved.
- Rejected records live outside the store, so quarantine trends over runs need extra plumbing.
- Less transferable: few teams run SQL through a homemade runner.

### Option 3: Lakehouse files (Parquet + Polars + Pandera)

Each layer is a Parquet folder partitioned by run; Polars transforms; Pandera validates dataframes; no database.

**Pros**:
- Matches modern lake layouts; Polars is fast and pleasant.
- No database file lock.

**Cons**:
- Idempotent loads, atomic writes across files, and quarantine tables are all hand built.
- Row level quarantine with reasons is awkward in dataframe schema validation.
- Transformations are Python dataframe code, not SQL, so they transfer less to warehouse SQL work.

### Option 4: Production shaped services (Redpanda streaming + PostgreSQL + Dagster, in Docker)

The generator publishes to a broker, a consumer loads into Postgres, and Dagster orchestrates with a UI.

**Pros**:
- Closest to a real production topology; teaches brokers, consumers, and orchestration UIs.
- Natural fit for the deferred incremental runs.

**Cons**:
- Docker plus three services before Release 1 produces a single report.
- Postgres is weaker than DuckDB for analytical scans at this size.
- Streaming makes deterministic, comparable runs much harder, which works against a core design value.

## Rationale

Option 1 is the only option that serves both forces that matter most: **transferable patterns** and **zero operations on one laptop**. Option 4 teaches real topology but spends each session on infrastructure, and streaming undermines the **determinism** the generator was designed around (basis: batch before stream unless latency forces it). Option 3 is realistic, but it hands you idempotency, atomicity, and quarantine to build by hand, which is exactly the plumbing Releases 1 to 3 need to be solid.

Option 2 is the strongest runner up and a fair choice if you wanted to avoid dbt. It loses on **runs must be comparable** and **bad data must be kept**: when validation happens in Python before load, rejected rows never reach the store, so quarantine trends need a second storage path. Validating in SQL from an append only raw layer keeps every record, every run, and every rejection reason in one queryable place (basis: immutable raw layer, medallion layering). dbt then supplies ordering, tests, and docs, the parts Option 2 would rebuild.

Smaller calls follow the same forces. Full rebuilds instead of incremental models, because the data is **small** and correctness over speed matters while the layers are new. Make instead of Dagster or Prefect, because three sequential steps do not justify a service (basis: add orchestration when a measured need appears). A config snapshot table instead of dbt vars, so each run carries its own config for Releases 3 and 4. Python 3.12 with uv, because the **aging runtime** would otherwise pin you to old DuckDB and dbt releases.

## References

**Project sources**:
- `docs/scope/scope.md`: releases 1 to 4, their done criteria, and the Skateboard build approach
- `README.md` *Design decisions*: determinism, events carry no dimensions, ordered baseline for out of order injection
- `common.py`: the Python 3.9 `Z` suffix workaround, and the argparse and stdout/stderr conventions
- `.claude/skills/` dbt Labs skills installed during this design

**Practices & standards**:
- Medallion (raw, staging, marts) layering
- Immutable, append only raw layer with idempotent, content hashed loads
- Batch before streaming unless latency or volume forces it
- Add orchestration and incremental processing only for a measured need

**Links**:
- DuckDB on PyPI: https://pypi.org/project/duckdb/
- DuckDB Python releases: https://github.com/duckdb/duckdb-python/releases
- dbt-duckdb on PyPI: https://pypi.org/project/dbt-duckdb/
- Polars on PyPI: https://pypi.org/project/polars/
- Pandera on PyPI: https://pypi.org/project/pandera/
- Pydantic releases: https://github.com/pydantic/pydantic/releases
- jsonschema on PyPI: https://pypi.org/project/jsonschema/
- Ruff on PyPI: https://pypi.org/project/ruff/
- Fivetran contributes SQLMesh to the Linux Foundation: https://www.fivetran.com/press/fivetran-contributes-sqlmesh-to-the-linux-foundation-to-advance-open-data-infrastructure
- Prefect acquires Dagster (press release): https://www.businesswire.com/news/home/20260713065285/en/Prefect-Acquires-Dagster-Uniting-the-Two-Leading-Modern-Orchestrators
- dbt Labs agent skills: https://github.com/dbt-labs/dbt-agent-skills
- dbt MCP server: https://github.com/dbt-labs/dbt-mcp
- MotherDuck DuckDB MCP server: https://github.com/motherduckdb/mcp-server-motherduck

## Evidence: landscape scan (2026-09-17)

Summarized from a quick web check; full notes in `docs/.agent-cache/research/pipeline-stack.md`.

- DuckDB 1.5 line, actively released, Python 3.12 supported. Polars 1.x stable.
- dbt Core with the DuckDB adapter supports Python 3.12. dbt Labs and Fivetran have merged; no dbt Core license change was found.
- SQLMesh was donated by Fivetran to the Linux Foundation and is still active.
- Prefect reportedly acquired Dagster in July 2026, with Dagster continuing as a product. Only the press release was checked.
- Ruff is the common lint and format tool; pytest remains the standard runner.
