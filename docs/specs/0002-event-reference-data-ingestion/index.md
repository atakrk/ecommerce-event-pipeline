# 0002. Load events and reference data into DuckDB, join them in dbt

**Date**: 2026-09-17
**Status**: In Progress

## Summary

This builds the first real step of the pipeline: a Python command that reads the three generated JSONL files into DuckDB, and the first dbt models that turn those raw lines into typed tables and one table where every event already carries its user and product details. The loader keeps every line exactly as it arrived and records what each run did, so a run can be repeated safely and never loads the same file twice. dbt does all the typing, deduplicating and joining, which is where that work belongs and where features 2 and 3 will extend it.

## Requirements

**User stories**:

- As the pipeline owner, I want one command that loads the generated files into the warehouse, so later features query a real table instead of scanning flat files.
- As the pipeline owner, I want to rerun that command safely, so a repeated or interrupted run never duplicates or corrupts data.
- As an analyst of this data, I want one table at event grain that already carries user and product attributes, so funnel questions are a single query with no joins to remember.
- As the pipeline owner, I want every run to record what it loaded and what produced that data, so a surprising number later can be traced back to a run.

**Acceptance criteria** (the contract, each independently checkable):

- **AC-1**: `make load` with no arguments reads `events.jsonl`, `users.jsonl` and `products.jsonl` from `paths.data_dir` (plus `events.manifest.json` when it exists) and writes one row per non empty line into `raw.events`, `raw.users` and `raw.products`, each row carrying its `load_id`, `source_file`, 1 based `line_number` matching the real file line, and the untouched `raw_line`.
- **AC-2**: rerunning `make load` on unchanged files loads nothing. Each file whose `content_sha256` already appears in `meta.load_files` is recorded with status `skipped_duplicate` and adds no raw rows. `--force` loads such a file anyway.
- **AC-3**: every run takes one `load_id` from `meta.load_id_seq` and writes one `meta.load_runs` row, committed with status `running` before any data is written. The raw rows, the `meta.load_files` rows, the `meta.run_config` rows **and the update to status `succeeded`** commit together as one transaction, or none of them do. A run that fails is marked `failed` in a separate transaction after its data has rolled back.
- **AC-4**: `marts.fct_events` holds exactly one row per distinct `event_id` across all loads, carrying `event_id`, `session_id`, `user_id`, `event_type`, `event_time`, `product_id`, `load_id`, plus `user_country`, `user_city`, `user_device_type`, `user_created_at`, `product_category` and `product_price`.
- **AC-5**: `python -m generator.run --manifest PATH` writes the run's effective settings to `PATH`, and the loader stores them as `meta.run_config` rows with `source = 'manifest'` beside the `config` rows taken from `config.yaml`. The same seed and arguments produce a byte identical manifest file.
- **AC-6**: a missing or unreadable expected input file aborts the run with a message naming the path, before any transaction opens and before any `load_runs` row exists. A missing manifest is not an error: the run warns on stderr and loads without manifest rows.
- **AC-7**: when another process holds `data/warehouse.duckdb`, the loader exits non zero with a message naming the file and saying to close the DuckDB MCP server or the DuckDB UI.
- **AC-8**: on success the loader writes to stderr one line per file (kind, path, lines loaded, or that it was skipped and why), then a total and the `load_id`. It writes nothing to stdout.
- **AC-9**: an event whose `user_id` or `product_id` matches no loaded reference row still appears in `marts.fct_events`, with the corresponding attributes null.
- **AC-10**: `make dbt-build` passes with the models' tests: `event_id` unique and not null in `marts.fct_events`, the required event fields not null, `event_type`, `device_type`, `country` and `category` within their accepted values, the composite grain of the versioned reference models unique, and a warn severity test per staging model reporting any raw lines that did not reach it.
- **AC-11**: `make pipeline` runs the load then `dbt build`. `make check` generates a small seeded dataset in a temporary folder, loads it into a temporary warehouse and builds dbt against that, leaving `data/warehouse.duckdb` untouched.

## Decision

**Chosen option**: Option 1: thin loader, everything else in dbt

`pipeline/load.py` copies each input file into `raw.<kind>` as untouched text lines with exact line numbers and records the run in `meta`, and dbt does all parsing, typing, deduplicating and joining in `staging` and `marts`.

**Implementation skills**: `using-dbt-for-analytics-engineering` (`dbt-labs/dbt-agent-skills`, `.claude/skills/using-dbt-for-analytics-engineering/`) · `running-dbt-commands` (`dbt-labs/dbt-agent-skills`, `.claude/skills/running-dbt-commands/`) · `adding-dbt-unit-test` (`dbt-labs/dbt-agent-skills`, `.claude/skills/adding-dbt-unit-test/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### Data model

**Loader owned, written by `pipeline/load.py`.** The loader creates these with `CREATE SCHEMA IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS` at the start of every run.

`meta.load_id_seq`: a DuckDB sequence, the only source of `load_id`.

| Table | Grain / key | Columns |
|---|---|---|
| `raw.events`, `raw.users`, `raw.products` | PK (`load_id`, `line_number`) | `load_id` BIGINT (FK `meta.load_runs`), `source_file` VARCHAR, `line_number` BIGINT, `raw_line` VARCHAR |
| `meta.load_runs` | PK `load_id` | `load_id` BIGINT, `started_at` TIMESTAMP, `ended_at` TIMESTAMP null, `status` VARCHAR, `error_message` VARCHAR null, `total_rows` BIGINT null |
| `meta.load_files` | PK (`load_id`, `file_kind`) | `load_id` BIGINT, `file_kind` VARCHAR, `source_file` VARCHAR, `content_sha256` VARCHAR, `line_count` BIGINT, `status` VARCHAR |
| `meta.run_config` | PK (`load_id`, `source`, `key`) | `load_id` BIGINT, `source` VARCHAR (`config` or `manifest`), `key` VARCHAR (dotted path), `value` VARCHAR |

`file_kind` is one of `events`, `users`, `products`, `manifest`. `meta.load_files.status` is `loaded` or `skipped_duplicate`.

**dbt owned.** All four models are materialized as tables and cover every load, per spec 0001.

| Model | Schema | Grain | Columns |
|---|---|---|---|
| `stg_raw__events` | `staging` | one row per raw line that parsed and cast, **not deduplicated** | `event_id`, `session_id`, `user_id`, `event_type`, `event_time` TIMESTAMP, `product_id` null, `load_id`, `line_number` |
| `stg_raw__users` | `staging` | one row per (`user_id`, `load_id`) | `user_id`, `country`, `city`, `device_type`, `created_at` TIMESTAMP, `load_id` |
| `stg_raw__products` | `staging` | one row per (`product_id`, `load_id`) | `product_id`, `category`, `price` DECIMAL(10,2), `load_id` |
| `fct_events` | `marts` | one row per `event_id`, deduplicated here | the seven `stg_raw__events` columns except `line_number`, plus `user_country`, `user_city`, `user_device_type`, `user_created_at`, `product_category`, `product_price` |

**Deduplication happens in `fct_events`, not in staging.** Staging is one to one with the raw lines that parsed, so the raw to staging row count test means exactly one thing (a parse or cast failure) for all three models. `fct_events` then keeps one row per `event_id`, first seen wins. A useful side effect: `count(stg_raw__events) - count(fct_events)` is the duplicate count, which is the metric feature 5 wants.

**Relationships**

- `meta.load_runs` 1:N `meta.load_files`, 1:N `meta.run_config`, 1:N each `raw.*` table.
- each `raw.*` table maps one to one onto its staging model, minus only the lines that fail to parse or cast.
- `stg_raw__events` N:1 `stg_raw__users` and N:1 `stg_raw__products`. Both are **optional** and both resolve **as of the load**: the version whose `load_id` is the greatest at or before the event's `load_id`. No such version leaves the attributes null.

### State transitions

A load run: `running` → `succeeded`, or `running` → `failed`.

- `running` is committed on its own, after every pre flight check has passed and immediately after `nextval` takes the `load_id`.
- The move to `succeeded` is **inside the data transaction**, so the data and the final status become visible in the same commit.
- `failed` is committed on its own *after* the data transaction has rolled back, so it is only ever written when there is no data.

That ordering is what makes the run table trustworthy: a row left at `running` means the process died or is still in flight, and **no data from that run exists**, because the only commit that would have written data would also have moved the status. Writing `succeeded` in its own commit after the data, as an earlier draft did, leaves a window where committed data sits under a permanently `running` row.

### Interface surface

This is a CLI feature, so the surface is commands, not endpoints.

| Command | Inputs | Outputs | Key errors |
|---|---|---|---|
| `python -m pipeline.load` | `--config PATH` (default `config.yaml`), `--database PATH`, `--manifest PATH`, `--force`, optional positional file paths overriding the config defaults | stderr summary per file, plus total and `load_id`; rows in `raw.*` and `meta.*` | missing or unreadable input file (abort before any write), warehouse locked by another process, invalid config value (`ConfigError`) |
| `python -m generator.run --manifest PATH` | the existing options plus `--manifest PATH` | events on stdout as today, plus the manifest JSON at `PATH` | unwritable manifest path |
| `make load` | `ARGS="..."` passed through | as `pipeline.load` | as above |
| `make pipeline` | none | `make load` then `make dbt-build` | either step's errors, and the pipeline stops there |

**Warehouse path precedence**: `--database`, then `DBT_DUCKDB_PATH`, then `config.paths.warehouse`, then `data/warehouse.duckdb`. `paths.warehouse` is added to `config.yaml` with the default value.

### Implementation mechanics

These were checked against the pinned DuckDB 1.5.5 in this project, not assumed. They are pinned here because each one has a plausible looking alternative that is wrong.

- **Loading a file is one SQL statement.** The file never passes through Python except to be hashed. One `INSERT INTO raw.<kind> SELECT ... FROM read_text(?)`, splitting on `chr(10)` and numbering with `generate_subscripts`, keeps spec 0001's rule that Python never inserts row by row, and needs no pyarrow or pandas.
- **A malformed line must be filtered before it is read.** `json_extract_string` raises `InvalidInputException` on invalid JSON, it does not return null, so a staging model that extracts first aborts the whole `dbt build` on the first bad line. Filter with `json_valid(raw_line)` in its own import CTE, then extract in the next one. Casts then use `try_cast`, which returns null on failure.
- **A present value that will not cast is what gets dropped.** A JSON `null` extracts as SQL null and is kept; only a non null value whose `try_cast` returns null is a cast failure. Those two cases must not be collapsed, or feature 2 loses the distinction between malformed and merely invalid.
- **The as of join is `ASOF LEFT JOIN`.** DuckDB expresses "the version with the greatest `load_id` at or before this one" natively: `asof left join stg_raw__users u on e.user_id = u.user_id and e.load_id >= u.load_id`. It must be the `LEFT` form, or AC-9's orphan events are silently dropped. Do not hand build a window function or correlated subquery for this.
- **`CAST('2026-09-01T00:00:00Z' AS TIMESTAMP)` is correct here.** Verified: DuckDB parses the ISO 8601 `Z` suffix and yields the right instant, and the dbt profile already sets the session time zone to UTC. No string surgery on the timestamp is needed.
- **`try_cast(... AS DECIMAL(10,2))` rounds rather than rejects.** `'12.345'` becomes `12.35`. A price with extra precision is accepted, not dropped; only a non numeric price is a cast failure.
- **Lock detection catches an exception class, not a message.** Catch `duckdb.IOException` and re-raise it as the guidance message. Never match on the error text, which is not a stable interface.
- **`nextval` is called only after every pre flight check passes.** Verified: DuckDB sequence values are not rolled back, so a `load_id` taken before validation would be burned with no record it ever existed. Taking it last means an AC-6 abort consumes nothing.
- **The manifest is written atomically with fixed serialization.** `json.dumps(..., indent=2, ensure_ascii=False)` over an insertion ordered dict, plus a trailing newline, through a new `common.write_json` that mirrors `write_jsonl`'s `.tmp` then `os.replace`. Key order, indentation and the final newline are all part of the byte identical guarantee in AC-5.
- **`make check` must override the exported warehouse path at the call site.** `DBT_DUCKDB_PATH` is exported once with `:=` at the top of the Makefile, so a nested `$(MAKE) dbt-build` inherits the real warehouse. Pass `DBT_DUCKDB_PATH=$$tmp/warehouse.duckdb $(MAKE) dbt-build` and give the loader `--database $$tmp/warehouse.duckdb`, so both halves point at the same temporary file.

### Value sourcing

| Action | Value produced | Source |
|---|---|---|
| load run | `load_id` | `nextval('meta.load_id_seq')`, called only after every pre flight check has passed |
| load run | `started_at`, `ended_at` | wall clock UTC at load time. Load metadata is not generator output, so the project's determinism rule does not apply to it |
| load run | `status` | `running` at start, then `succeeded`, or `failed` when an exception is caught |
| load run | `error_message` | the caught exception's message, null on success |
| load run | `total_rows` | sum of that run's `meta.load_files.line_count` |
| per file | `file_kind` | the config key the path came from; for an explicit positional path, the file name stem (`events.jsonl` → `events`) |
| per file | `content_sha256` | SHA 256 over the file's bytes as read |
| per file | `line_count` | number of rows actually written to `raw` for that file |
| per file | `status` | `skipped_duplicate` when the hash is already in `meta.load_files` and `--force` was not given, else `loaded` |
| raw row | `line_number` | 1 based position in the newline split of the file text, assigned before empty lines are dropped, so it always matches the real file line |
| raw row | `source_file` | the resolved absolute path, not the string as typed |
| raw row | `raw_line` | the split element with any trailing `\r` removed and nothing else changed |
| `meta.run_config` | `config` rows | `config.yaml` as read for this run, flattened to dotted keys; a list or mapping leaf is stored as its JSON text |
| `meta.run_config` | `manifest` rows | `events.manifest.json` flattened the same way; absent when no manifest was found |
| manifest | `seed`, `sessions`, `start`, `window_hours`, the four funnel rates, `sessions_written`, `events_written` | the generator's resolved settings and its own deterministic output counts. No wall clock, no git state |
| `stg_raw__*` | typed columns | `json_valid` filter, then `json_extract_string(raw_line, '$.<field>')` and `try_cast`; `event_time` and `created_at` to TIMESTAMP, `price` to DECIMAL(10,2) |
| `fct_events` | which copy of a duplicated event survives | lowest `load_id`, then lowest `line_number`, per `event_id` |
| `fct_events` | `user_*` columns | the `stg_raw__users` row for that `user_id` with the greatest `load_id` at or before the event's `load_id`; null when none exists |
| `fct_events` | `product_*` columns | the same rule on `stg_raw__products` |
| warehouse connection | database path | `--database`, else `DBT_DUCKDB_PATH`, else `config.paths.warehouse`, else `data/warehouse.duckdb` |

### Key invariants

- **Raw is text and is never touched again.** No update or delete on `raw.*`. Every correction happens downstream.
- **A file is loaded at most once.** Identity is the SHA 256 of its bytes, not its path or its modification time. `--force` is the only override.
- **A run always exists.** A run whose files are all skipped still takes a `load_id` and ends `succeeded` with `total_rows` 0. That is how "I ran it and nothing was new" stays visible.
- **Atomic data, visible failure.** The `running` row is committed before the data so the run is recorded at all; the data and the move to `succeeded` commit together; `failed` is written only after a rollback. A `running` row therefore always means no data from that run exists.
- **A row is dropped in staging only for a parse or cast failure.** The line is not valid JSON, or a value that is present will not cast. A JSON `null` is not a cast failure: it stays null and is caught by a `not_null` test, because semantic validation belongs to feature 2.
- **Staging is one to one with the raw lines that parsed.** No deduplication there, so a raw to staging row count gap has exactly one meaning. Deduplication is `fct_events`'s job.
- **Every staging and mart row carries its `load_id`**, and reference attributes are always resolved as of that load.
- **All timestamps are UTC.** The loader writes UTC; dbt's profile already sets the session time zone to UTC.
- **dbt reads the loader only through a declared source** named `raw`, never by hard coded table name.

### Security model

Not applicable in the usual sense. All data is synthetic, contains no personal data, and never leaves the machine. There is no authentication, no authorization, and no network surface. The only access control is the file system, and the only real risk this feature carries is DuckDB's single writer lock, handled in AC-7.

### Configuration required

No secrets and no new environment variables. Two additions to existing configuration:

- `config.yaml` gains `paths.warehouse`, defaulting to `data/warehouse.duckdb`, so the warehouse path lives with every other path.
- `DBT_DUCKDB_PATH`, which Make already exports for dbt, is now also read by the loader, so `make pipeline` and `make check` point both halves at the same file.

### Critical test scenarios

- Happy path: load the three fixture files into a temporary warehouse, then `dbt build`, and assert `fct_events` has one row per fixture event with its user and product attributes filled. Verifies **AC-1**, **AC-4**, **AC-11**.
- Idempotency: run the same load twice and assert the second run adds no raw rows, records every file as `skipped_duplicate`, and still creates a `load_runs` row; then run with `--force` and assert the rows are loaded. Verifies **AC-2**, **AC-3**.
- Failure recorded: make the data transaction fail partway and assert the `load_runs` row exists with status `failed` and an `error_message`, while `raw.*` gained nothing. Verifies **AC-3**.
- Crash window: kill the process between the data transaction and any later write, and assert the warehouse never shows committed raw rows under a `running` run row. Verifies **AC-3**.
- Missing input: delete `products.jsonl` and assert the run aborts naming that path, with no `load_runs` row and no partial data. Verifies **AC-6**.
- Missing manifest: load with no `events.manifest.json` present and assert the run succeeds, warns on stderr, and writes `config` rows but no `manifest` rows. Verifies **AC-5**, **AC-6**.
- Locked warehouse: hold the DuckDB file open in another connection and assert the loader exits non zero with the message naming the file and the MCP server or UI. Verifies **AC-7**.
- Orphan reference: load an events file naming a `user_id` absent from `users.jsonl` and assert the event is still in `fct_events` with null user attributes, and that the relationships test warns rather than fails. Verifies **AC-9**, **AC-10**.
- Deduplication: load the same events twice with `--force` and assert `stg_raw__events` holds both copies while `fct_events` keeps exactly one row per `event_id`, carrying the lower `load_id`, and that the raw to staging count test still reports no gap. Verifies **AC-4**, **AC-10**.
- Malformed line: put a line of invalid JSON in the events fixture and assert `dbt build` completes (rather than aborting on the extract), the row is absent from staging, and the count test warns about exactly one missing line. Verifies **AC-10**.
- Determinism: generate twice with the same seed and assert both manifest files hash identically, pinned in `tests/test_determinism.py` beside the existing golden hashes. Verifies **AC-5**.
- Output discipline: assert the loader's stdout is empty and its stderr carries one line per file plus the total and `load_id`. Verifies **AC-8**.

## Build plan

Ordered for **Skateboard**: task 1 alone gives you a working, queryable warehouse from one command. Each later task widens that same whole rather than adding a layer that is unusable until the next one lands.

1. Create `pipeline/` with `load.py`: config and argument validation, warehouse path precedence, schema and table creation, the `load_runs` lifecycle, and loading `events.jsonl` only into `raw.events` and `meta.load_files` via `read_text` plus a newline split. Add the `make load` target. Satisfies **AC-1** (events), **AC-3**, **AC-6** (missing file), **AC-7**, **AC-8**.
2. Widen the loader to `users.jsonl` and `products.jsonl`, and add content hash skipping plus `--force`. Satisfies **AC-1**, **AC-2**.
3. Add `--manifest PATH` to `generator/run.py` writing the deterministic settings file, have `make events` pass `data/events.manifest.json`, and load both `config.yaml` and the manifest into `meta.run_config`. Satisfies **AC-5**, **AC-6** (absent manifest).
4. Write the loader's pytest suite: unit tests for hashing, config flattening and path precedence, plus end to end loads against a temporary DuckDB covering first load, skip, `--force`, missing file, absent manifest and a recorded failure. Pin the manifest hash in `tests/test_determinism.py`. Satisfies **AC-1**, **AC-2**, **AC-3**, **AC-5**, **AC-6**, **AC-7**, **AC-8**.
5. Declare the dbt source `raw` over the three raw tables and build `stg_raw__events`: the `json_valid` import CTE, extraction and `try_cast`, and the present but uncastable filter. No deduplication here. Satisfies **AC-4**.
6. Build `stg_raw__users` and `stg_raw__products` the same way, keeping every loaded version at (`id`, `load_id`) grain. Satisfies **AC-4**.
7. Build `marts.fct_events`: deduplicate on `event_id` first seen wins, then the two `ASOF LEFT JOIN`s onto the reference models. Satisfies **AC-4**, **AC-9**.
8. Add the dbt tests: `unique` and `not_null` on `fct_events.event_id`, `not_null` on the required event fields (not `product_id`), accepted values, `dbt_utils.unique_combination_of_columns` on the reference grains, the relationships tests at warn, and one singular test per staging model comparing its row count to the matching raw count per `load_id`. Satisfies **AC-10**.
9. Add `make pipeline`, and extend `make check` to load the seeded temporary dataset into a temporary warehouse and build dbt against it, overriding `DBT_DUCKDB_PATH` at the nested `$(MAKE)` call site so `data/warehouse.duckdb` is never touched. Satisfies **AC-11**.

## Consequences

**Positive**:

- One command takes you from generated files to a queryable event table, and repeating it is always safe.
- Feature 2 has an obvious next move: turn the staging filter and the warn severity count tests into real quarantine models, with no restructuring.
- Feature 5 gets its duplicate rate almost free, because deduplication happens in the fact and staging keeps every copy: the difference between the two row counts is the metric.
- Feature 3 gets `fct_events` and writes only funnel logic on top of it.
- Every run is traceable: what was loaded, from which file, with which hash, under which settings.
- The dbt layer stops being an empty skeleton and gains its first source, models and tests, which is the part of this stack most worth learning.

**Negative / tradeoffs**:

- Keeping every reference version and joining as of costs a window function in `fct_events` for a case that cannot happen yet, since reference data is generated once and `ensure_writable` refuses to overwrite it without `--force`. That complexity is bought for feature 4, not for today.
- `not_null` on the required event fields is at error severity, so once feature 4 injects malformed events the dbt build will fail rather than quarantine. **Feature 2 must land before feature 4.**
- Storing raw as text means every cast lives in staging SQL, and a cast failure is invisible except as a row count gap until feature 2 gives it a reason.
- `read_text` holds the whole file in memory, roughly 30 MB at the current event volume. Comfortable now, and something to revisit if event volumes grow by an order of magnitude.
- The accepted values lists in the dbt tests duplicate `generator.session.EVENT_TYPES` and the `config.yaml` country, device and category keys. SQL cannot import them, so they can drift.
- Two commits per run instead of one, because the `running` row is written before the data transaction. Slightly more code and one more ordering rule to respect, in exchange for failures that are visible at all.
- `stg_raw__events` is not unique on `event_id`, which will surprise anyone who expects a staging model to be a clean key. It is the price of making the raw to staging count test mean one unambiguous thing, and the uniqueness guarantee simply moves to `fct_events` where it is tested.
- `make check` gets slower: it now loads and builds as well as generating and verifying.

**Neutral**:

- `config.yaml` gains `paths.warehouse`; nothing else in it changes.
- `generator/run.py` gains `--manifest`, which writes nothing unless the option is passed, so existing calls behave identically.
- A new `pipeline/` package with `__init__.py`, matching the existing `reference/` and `generator/` layout.
- `common.py` gains `write_json`, the single object counterpart to `write_jsonl`, with the same `.tmp` then `os.replace` atomic write.
- `data/events.manifest.json` joins the generated files; `make clean-data` should remove it along with the JSONL files.
- The dropped row count tests are deliberately temporary. Feature 2 replaces them rather than adding to them.

## Follow-up

- [ ] `transform/AGENTS.md` still says `models/staging/` and `models/marts/` are empty until feature 1. Run `/sync` after the build so it describes the real models, the `raw` source and the naming in use.
- [ ] The accepted values in the dbt tests duplicate `generator.session.EVENT_TYPES` and the `config.yaml` category, country and device keys. Consider generating the test YAML or reading the values from `meta.run_config` once feature 5 makes run settings a real source.
- [ ] Spec 0001's follow up about connecting the DuckDB and dbt MCP servers is still open, and both must be closed before `make pipeline`. AC-7 makes that failure legible, but it does not remove the need.
- [ ] `docs/` is currently untracked in git. Commit it so specs and scope are versioned with the code they govern.
