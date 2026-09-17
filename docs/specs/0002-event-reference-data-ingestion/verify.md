# Verify: Event and reference data ingestion · spec 0002 · updated 2026-09-17

_Steps derived from spec 0002 acceptance criteria and its Value sourcing table. `/check verify` runs these; `/test` locks the durable ones._

Most steps run against a throwaway warehouse so `data/warehouse.duckdb` is never disturbed. A handy setup:

```bash
tmp=$(mktemp -d)
sed "s#^  data_dir: .*#  data_dir: $tmp#" config.yaml > $tmp/config.yaml
uv run python -m reference.generate_users --config $tmp/config.yaml
uv run python -m reference.generate_products --config $tmp/config.yaml
uv run python -m generator.run --config $tmp/config.yaml --sessions 500 \
    --manifest $tmp/events.manifest.json > $tmp/events.jsonl
load() { uv run python -m pipeline.load --config $tmp/config.yaml --database $tmp/wh.duckdb "$@"; }
ask() { uv run python -c "import duckdb,sys; print(duckdb.connect('$tmp/wh.duckdb').execute(sys.argv[1]).fetchall())" "$1"; }
```

## Commands

- [x] `load` → stderr lists users, products, events and the manifest; `ask "select count(*) from raw.events"` matches `wc -l < $tmp/events.jsonl` → AC-1
- [x] `ask "select load_id, source_file, line_number, raw_line from raw.events order by line_number limit 3"` → every row carries its load, its absolute source path and a 1-based line number → AC-1
- [x] `load` a second time → every file reported as skipped; `ask "select status from meta.load_files where load_id = 2"` is all `skipped_duplicate`, and `raw.events` has not grown → AC-2
- [x] `load --force` → the same rows land again under a new `load_id` → AC-2
- [x] `ask "select load_id, status, total_rows from meta.load_runs"` → one row per run; `total_rows` equals the sum of that run's `meta.load_files.line_count` → AC-3
- [x] `ask "select count(*) from meta.load_runs where status = 'running'"` is 0 after every finished run → AC-3
- [x] `rm $tmp/products.jsonl && load` → exits 2 naming that path, and `meta.load_runs` gains no row → AC-6
- [x] `rm $tmp/events.manifest.json && load --force` → warns on stderr, still exits 0, writes `config` rows but no `manifest` rows → AC-5, AC-6
- [x] hold the warehouse open in another process, then `load` → exits non zero naming the file and telling you to close the DuckDB MCP server or UI → AC-7
- [x] `load > /tmp/out.txt` → `/tmp/out.txt` is empty, and stderr has one line per file plus a total and `load_id` → AC-8
- [x] `DBT_DUCKDB_PATH=$tmp/wh.duckdb make dbt-build` → `PASS`, no `ERROR` → AC-10
- [x] `make pipeline` on the real `data/` → load then dbt build, both green → AC-11
- [x] `make check` → green, and `shasum data/warehouse.duckdb` is unchanged before and after → AC-11
- [x] `make verify` and `select event_type, count(distinct session_id) from marts.fct_events group by 1` report the same funnel counts on the same seeded data → AC-4

## Data checks (after a dbt build)

- [x] `ask "select count(*) from marts.fct_events"` equals `ask "select count(distinct event_id) from staging.stg_raw__events"` → AC-4
- [x] `ask "select * from marts.fct_events limit 1"` shows `user_country`, `user_city`, `user_device_type`, `user_created_at`, `product_category`, `product_price` filled for a normal event → AC-4
- [x] load an events file naming a `user_id` and `product_id` that no reference row has → that event is still in `fct_events`, with those attributes null, and the relationships tests warn rather than fail → AC-9, AC-10
- [x] load the same events twice with `--force` → `stg_raw__events` holds both copies, `fct_events` keeps one row per `event_id` carrying the **lower** `load_id`, and the coverage test still reports no gap → AC-4, AC-10
- [x] put a line of invalid JSON in the events file and rebuild → `dbt build` completes rather than aborting, the row is absent from staging, and `stg_raw__events_covers_every_raw_line` warns about exactly that one line → AC-10

## Value sourcing (each row of the spec's table, at the edge that breaks)

- [x] `load_id` comes from `meta.load_id_seq` **after** the pre flight checks: an aborted run (missing input) must not consume one, so the next successful run's `load_id` is the one that was next before the failure → AC-3, AC-6
- [x] `content_sha256` identifies a file by its **bytes**, not its path or mtime: `touch $tmp/events.jsonl && load` still skips it, while appending one line makes it load again without `--force` → AC-2
- [x] `line_number` is assigned **before** blank lines are dropped: put a blank line 2 into the events file, reload with `--force`, and the following record still reports line 3 → AC-1
- [x] `raw_line` strips only a trailing carriage return: a file written with CRLF endings stores lines with no `\r` and nothing else changed → AC-1
- [x] `source_file` is the resolved absolute path, not the string as typed: `load ../<repo>/data/events.jsonl` still records an absolute path → AC-1
- [x] `file_kind` for a positional path comes from the file name stem: `load $tmp/events.jsonl` records kind `events` → AC-1
- [x] `meta.run_config` `config` rows are `config.yaml` flattened to dotted keys, with a list leaf kept as JSON text: check `users.countries.TR.cities` → AC-5
- [x] manifest rows are byte identical across runs: generate twice with the same seed and arguments and compare the two manifest files' hashes → AC-5
- [x] `fct_events` survivor is the **lowest** `load_id`, then the lowest `line_number`: after a forced reload, every row's `load_id` is the earlier one → AC-4
- [x] reference attributes resolve **as of the load**, not the newest: change a user's country, load it as a later run, and confirm events from the earlier load still show the old country while the new load's events show the new one → AC-4
- [x] a failed data transaction leaves `status = 'failed'` with an `error_message`, and no raw rows or `load_files` rows from that run → AC-3
- [x] warehouse path precedence holds: `--database` beats `DBT_DUCKDB_PATH`, which beats `config.paths.warehouse`, which beats `data/warehouse.duckdb` → AC-1
- [x] `started_at` and `ended_at` are wall clock UTC (load metadata is not generator output, so determinism does not apply to them) → AC-3

## Acceptance-criteria coverage

- AC-1 · raw rows, line numbers, source path, raw line · AC-2 · hash skipping and `--force` · AC-3 · run lifecycle and atomicity · AC-4 · `fct_events` grain and attributes · AC-5 · manifest written, loaded and deterministic · AC-6 · missing input aborts, missing manifest warns · AC-7 · locked warehouse · AC-8 · stderr only · AC-9 · orphan references kept · AC-10 · dbt tests · AC-11 · `make pipeline` and `make check`

## Crash window (run by hand, not yet automated)

- [x] `SIGKILL` the loader partway through a load, then reopen the warehouse → the run row sits at `status = 'running'` with `total_rows` null, and `raw.events`, `raw.users`, `raw.products` and `meta.load_files` all hold nothing from that run → AC-3

  Verified 2026-09-17 against a fresh warehouse: killed 0.6s into a 187,353 row load, and the reopened file showed `[(1, 'running', None)]` with 0 rows everywhere. Still worth an automated test, which is `/test`'s call.
