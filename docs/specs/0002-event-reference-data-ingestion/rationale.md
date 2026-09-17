# 0002. Rationale

The build spec is in [index.md](index.md). This file holds the reasoning, the options weighed, and the smaller calls made while writing.

## Context

The repository generates synthetic ecommerce data well and checks it with `verify.py`, but nothing has ever been stored. Every question about the data means re reading flat files, and `verify.py` answers only the questions someone hard coded into it. Spec 0001 decided the shape of everything downstream (raw, then staging with quarantine, then marts, in one DuckDB file, loaded by Python and transformed by dbt) and scaffolded it, but `transform/models/` is still empty and the loader does not exist. This feature is where the decided architecture becomes a working thing.

The forces that shaped this spec:

- **Spec 0001 already settled most of the structure**, including invariants this spec must not break: raw is append only text keyed by `load_id`, a file is never loaded twice, staging and marts rebuild in full from raw, reference data for an events load is the most recent copy at or before that load, and dbt reads the loader only through sources. The room left is how those invariants are realized and what belongs to this feature rather than the next two.
- **Three features share this ground.** Feature 2 owns validation and quarantine, feature 3 owns the funnel report, feature 4 will inject deliberate mess. Decisions here either leave those features a clean place to land or force them to rewrite. The boundary matters more than the code.
- **Spec 0001 explicitly deferred two things to this spec**: the event dedup key, and part of the quarantine rules.
- **Determinism is load bearing in this repo.** `tests/test_determinism.py` pins generator output with golden hashes and `AGENTS.md` states that the same seed gives byte identical output. Anything new the generator writes inherits that constraint.
- **This is a learning project.** The patterns being practised are worth more than the shortest path, but only where the pattern is real. Complexity with no lesson in it is just complexity.
- **It runs on one laptop.** 176 thousand events, 10 thousand users, 1 thousand products, one DuckDB file that allows a single writing process. There is no scale problem to solve and no team to coordinate.

Not deciding leaves the project stuck: features 2, 3, 5 and 6 all query a store that does not exist yet, and the dbt half of the stack stays a skeleton that has never been proven against real data.

## Options considered

### Option 1: thin loader, everything else in dbt

The loader copies each file into `raw.<kind>` as untouched text lines with exact line numbers and writes the run's metadata. dbt does all parsing, typing, deduplicating and joining.

**Pros**:

- Follows spec 0001's raw layer rule exactly, so a malformed line survives in the warehouse and stays measurable, which features 2 and 5 both need.
- The loader is small and its logic is easy to test: read bytes, hash, split, insert, record.
- All transformation logic is in version controlled SQL that dbt tests and documents, which is the transferable skill this project exists to build.

**Cons**:

- Every type cast lives in SQL, which is more verbose than letting a JSON reader infer types.
- A cast failure shows up only as a row count gap until feature 2 gives it a reason.
- Two languages and two test runners for one feature.

### Option 2: typed loader

The loader parses each JSON line in Python or through DuckDB's JSON reader and writes typed columns into `raw`, leaving dbt only the join.

**Pros**:

- Much less SQL, and type errors surface at load time with a Python traceback pointing at the offending line.
- Staging models become nearly pass through, so the first dbt models are trivial.

**Cons**:

- Breaks spec 0001's invariant that raw is an untouched copy. A line that fails to parse either aborts the load or is silently lost, and feature 5 can then never measure how often that happens.
- Pushes validation into Python, which is exactly the work feature 2 is supposed to do in SQL with quarantine tables.
- The loader grows a schema per file kind, so feature 4's new input file means changing Python rather than adding a model.

### Option 3: no dbt yet, Python builds the joined table

`pipeline/load.py` reads the files and writes `fct_events` directly with a single DuckDB query, leaving `transform/` empty for now.

**Pros**:

- The fastest route to a joined, queryable table, in one file with one test suite.
- No dbt concepts to hold while getting the first end to end path working.

**Cons**:

- Abandons the layered design of spec 0001 at the first opportunity, and feature 3 would have to rebuild the whole thing in dbt anyway.
- No lineage, no model level tests, no documentation, and the deduplication and as of join logic ends up embedded in a Python string.
- The dbt skeleton scaffolded in feature 0 stays unproven, so its first real test would come under the pressure of feature 3.

### Option 4: dbt reads the JSONL files directly, no loader

Use `dbt-duckdb`'s ability to treat an external file as a source and skip the Python loader entirely.

**Pros**:

- Genuinely the least code: no loader, no `raw` schema, no `meta` tables, just models over files.
- Nothing can get out of step between files and warehouse, because there is no copy.

**Cons**:

- There is no load history, so `load_id` has nothing to mean, and features 5 and 6 are built on comparing runs.
- No idempotency: rerunning reads whatever the file says now, and a regenerated file silently rewrites the past.
- The raw text of a malformed line is never preserved, so quarantine in feature 2 has nothing to quarantine from.
- Arrival order and line numbers, which feature 4 needs to measure out of order events, are not available.

## Rationale

Option 1 is the only one that keeps every invariant spec 0001 set, and those invariants are not decoration: each exists because a later release in the scope depends on it. Option 4 is tempting for how little code it is, and on a one off analysis it would be the right answer, but three of the six remaining features compare runs to each other, and that requires a stored, immutable copy with a run identity. Option 2 is the most natural instinct and the one that quietly costs the most later, because it moves validation into the loader and leaves feature 2 with nothing coherent to build. Option 3 trades the whole architecture for a few days.

The choices inside Option 1 mostly follow from the same reasoning. `event_id` alone as the dedup key works because the generator already mints a UUID per event, so a replayed duplicate in feature 4 carries the same id and is caught exactly as intended; a composite natural key would both collide legitimately (two product views in one second) and miss the case it exists to catch. First seen wins, on lowest `load_id` then lowest `line_number`, keeps an event's `load_id` stable as new loads arrive, which matters because feature 5 will count events per run.

Two decisions deserve their honest caveats. Keeping every reference version and resolving the join as of the event's load is more SQL than the data needs today, since reference data is generated once and `common.ensure_writable` refuses to regenerate it without `--force`. It is in because spec 0001 states it as an invariant every feature must keep, and because feature 4's late arriving dimension changes are precisely this machinery. If that feature is ever dropped, this is the first thing to simplify. Separately, committing the `running` row before the data transaction slightly weakens the "one load is one transaction" line in spec 0001. The data is still strictly all or nothing; only the fact that a run was attempted is committed separately. Without that split a failed run leaves no trace at all, which makes a run table that can only record successes, and the point of a run table is answering how often things go wrong.

## What the cross check changed

An independent review of the first draft found two design faults and a set of unpinned mechanisms. Both faults are worth recording, because in each case the first answer was the intuitive one.

**The run table could lie.** The first draft committed the `succeeded` update in its own transaction *after* the data transaction, and then claimed a row stuck at `running` proved nothing had been written. It proved no such thing: a hard kill between those two commits leaves committed data under a permanently `running` row, which is precisely the case the run table exists to make visible. Moving the `succeeded` update inside the data transaction fixes it completely, because the only commit that writes data is now also the commit that clears `running`. `failed` stays outside, which is safe, since it is written only once the data has already rolled back.

**The dropped row test could not work where it mattered most.** The first draft deduplicated in `stg_raw__events` and then tested that model's row count against the raw count per load. Those two things contradict each other: a second load of the same events legitimately contributes zero surviving rows, so the test would either cry wolf on every healthy rerun or need an unwritten exception. Moving deduplication down into `fct_events` makes staging one to one with the lines that parsed, so a count gap has exactly one cause. It also turns out better than a workaround: the gap between the staging and fact row counts is now the duplicate count, which is a metric feature 5 was going to have to derive some other way.

The rest of the review was about mechanisms the draft left to the implementer, and each one had a plausible wrong answer: `json_extract_string` raising rather than returning null on malformed JSON, DuckDB's native `ASOF LEFT JOIN` versus a hand rolled window function, the bulk insert path that spec 0001's no row by row rule depends on, sequence values surviving a rollback, and `make check` inheriting the exported `DBT_DUCKDB_PATH` into its nested `$(MAKE)` call. All of them are now pinned in *Implementation mechanics* in the build spec, checked against the DuckDB version this project pins rather than asserted from memory. The one claim that turned out to need no change was the timestamp cast: DuckDB does parse the ISO 8601 `Z` suffix correctly, so that is recorded as verified so nobody spends time on it mid build.

The scope boundary was drawn to leave feature 2 a real feature. A row is dropped in staging only when the line does not parse or a present value will not cast. A row that parses but is semantically wrong flows through and is caught by a test. That has a consequence worth stating plainly, and it is in the spec's Consequences: with `not_null` at error severity, feature 4's injected mess would fail the build, so feature 2 has to land before feature 4. That ordering was already implied by the scope; this spec just makes it explicit.

## Smaller calls made while writing

These were decided here rather than asked, with the runner up noted.

- **Blank and trailing lines.** Number every element of the newline split first, then drop the empty and whitespace only ones, so `line_number` always matches the real file line and the trailing empty element of a file that ends in a newline disappears cleanly. Runner up: store blank lines as raw rows too, which is more faithful but adds noise that the row count tests would then have to subtract.
- **Line endings.** Strip a single trailing `\r` from each split element and change nothing else, so a file written on Windows does not produce unparseable JSON. Runner up: split on a regex covering both endings, which is slower in DuckDB for no extra correctness.
- **Load timestamps.** `started_at` and `ended_at` use wall clock UTC. The determinism rule in `AGENTS.md` governs generator output, not load metadata, and a run with no real time is useless for the "last N runs" questions in feature 5. Runner up: omit timestamps entirely and order by `load_id`, which loses duration.
- **Config flattening.** `config.yaml` is flattened to dotted keys with scalar values; a list or mapping leaf, such as a country's city list, is stored as its JSON text. Runner up: expand lists into indexed keys (`users.countries.TR.cities.0`), which reads badly and makes the row count depend on config size.
- **`file_kind` for explicit paths.** When a path is passed positionally, the kind comes from the file name stem. Runner up: require `kind=path` pairs on the command line, which is unambiguous but noisy for the common case where the names already match.
- **A run where everything is skipped** still takes a `load_id` and ends `succeeded` with `total_rows` 0, rather than exiting early with no record. Runner up: skip the run entirely, which hides the fact that you ran it.
- **The dbt source declares `raw` only**, not `meta`. Nothing in this feature's models reads `meta`, and declaring a source no model uses invites a stale declaration. Feature 5 adds `meta` when it needs `run_config`.
- **`product_id` is excluded from the `not_null` tests.** It is legitimately null for `PAGE_VIEW` events, which is the first event of every session.
- **The manifest records deterministic output counts** (`sessions_written`, `events_written`) but no wall clock and no git state, so it stays inside the byte identical guarantee and can be pinned in `tests/test_determinism.py` beside the existing hashes. Runner up: include `generated_at`, which is more useful for debugging one odd run and puts the file permanently outside the determinism tests.
