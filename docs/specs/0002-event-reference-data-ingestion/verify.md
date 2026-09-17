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

- [ ] `load` → stderr lists users, products, events and the manifest; `ask "select count(*) from raw.events"` matches `wc -l < $tmp/events.jsonl` → AC-1
- [ ] `ask "select load_id, source_file, line_number, raw_line from raw.events order by line_number limit 3"` → every row carries its load, its absolute source path and a 1-based line number → AC-1
- [ ] `load` a second time → every file reported as skipped; `ask "select status from meta.load_files where load_id = 2"` is all `skipped_duplicate`, and `raw.events` has not grown → AC-2
- [ ] `load --force` → the same rows land again under a new `load_id` → AC-2
- [ ] `ask "select load_id, status, total_rows from meta.load_runs"` → one row per run; `total_rows` equals the sum of that run's `meta.load_files.line_count` → AC-3
- [ ] `ask "select count(*) from meta.load_runs where status = 'running'"` is 0 after every finished run → AC-3
- [ ] `rm $tmp/products.jsonl && load` → exits 2 naming that path, and `meta.load_runs` gains no row → AC-6
- [ ] `rm $tmp/events.manifest.json && load --force` → warns on stderr, still exits 0, writes `config` rows but no `manifest` rows → AC-5, AC-6
- [ ] hold the warehouse open in another process, then `load` → exits non zero naming the file and telling you to close the DuckDB MCP server or UI → AC-7
- [ ] `load > /tmp/out.txt` → `/tmp/out.txt` is empty, and stderr has one line per file plus a total and `load_id` → AC-8
- [ ] `DBT_DUCKDB_PATH=$tmp/wh.duckdb make dbt-build` → `PASS`, no `ERROR` → AC-10
- [ ] `make pipeline` on the real `data/` → load then dbt build, both green → AC-11
- [ ] `make check` → green, and `shasum data/warehouse.duckdb` is unchanged before and after → AC-11
- [ ] `make verify` and `select event_type, count(distinct session_id) from marts.fct_events group by 1` report the same funnel counts on the same seeded data → AC-4

## Data checks (after a dbt build)

- [ ] `ask "select count(*) from marts.fct_events"` equals `ask "select count(distinct event_id) from staging.stg_raw__events"` → AC-4
- [ ] `ask "select * from marts.fct_events limit 1"` shows `user_country`, `user_city`, `user_device_type`, `user_created_at`, `product_category`, `product_price` filled for a normal event → AC-4
- [ ] load an events file naming a `user_id` and `product_id` that no reference row has → that event is still in `fct_events`, with those attributes null, and the relationships tests warn rather than fail → AC-9, AC-10
- [ ] load the same events twice with `--force` → `stg_raw__events` holds both copies, `fct_events` keeps one row per `event_id` carrying the **lower** `load_id`, and the coverage test still reports no gap → AC-4, AC-10
- [ ] put a line of invalid JSON in the events file and rebuild → `dbt build` completes rather than aborting, the row is absent from staging, and `stg_raw__events_covers_every_raw_line` warns about exactly that one line → AC-10

## Value sourcing (each row of the spec's table, at the edge that breaks)

- [ ] `load_id` comes from `meta.load_id_seq` **after** the pre flight checks: an aborted run (missing input) must not consume one, so the next successful run's `load_id` is the one that was next before the failure → AC-3, AC-6
- [ ] `content_sha256` identifies a file by its **bytes**, not its path or mtime: `touch $tmp/events.jsonl && load` still skips it, while appending one line makes it load again without `--force` → AC-2
- [ ] `line_number` is assigned **before** blank lines are dropped: put a blank line 2 into the events file, reload with `--force`, and the following record still reports line 3 → AC-1
- [ ] `raw_line` strips only a trailing carriage return: a file written with CRLF endings stores lines with no `\r` and nothing else changed → AC-1
- [ ] `source_file` is the resolved absolute path, not the string as typed: `load ../<repo>/data/events.jsonl` still records an absolute path → AC-1
- [ ] `file_kind` for a positional path comes from the file name stem: `load $tmp/events.jsonl` records kind `events` → AC-1
- [ ] `meta.run_config` `config` rows are `config.yaml` flattened to dotted keys, with a list leaf kept as JSON text: check `users.countries.TR.cities` → AC-5
- [ ] manifest rows are byte identical across runs: generate twice with the same seed and arguments and compare the two manifest files' hashes → AC-5
- [ ] `fct_events` survivor is the **lowest** `load_id`, then the lowest `line_number`: after a forced reload, every row's `load_id` is the earlier one → AC-4
- [ ] reference attributes resolve **as of the load**, not the newest: change a user's country, load it as a later run, and confirm events from the earlier load still show the old country while the new load's events show the new one → AC-4
- [ ] a failed data transaction leaves `status = 'failed'` with an `error_message`, and no raw rows or `load_files` rows from that run → AC-3
- [ ] warehouse path precedence holds: `--database` beats `DBT_DUCKDB_PATH`, which beats `config.paths.warehouse`, which beats `data/warehouse.duckdb` → AC-1
- [ ] `started_at` and `ended_at` are wall clock UTC (load metadata is not generator output, so determinism does not apply to them) → AC-3

## Acceptance-criteria coverage

- AC-1 · raw rows, line numbers, source path, raw line · AC-2 · hash skipping and `--force` · AC-3 · run lifecycle and atomicity · AC-4 · `fct_events` grain and attributes · AC-5 · manifest written, loaded and deterministic · AC-6 · missing input aborts, missing manifest warns · AC-7 · locked warehouse · AC-8 · stderr only · AC-9 · orphan references kept · AC-10 · dbt tests · AC-11 · `make pipeline` and `make check`

## Known gaps

- The spec's **crash window** scenario (kill the process between the data transaction and any later write, then confirm no committed raw rows sit under a `running` run row) is not covered by an automated test. The ordering that makes it true is in `pipeline/load.py` (`begin_run`, `load_all`, `fail_run`), and the invariant is checked indirectly by the `status = 'running'` count above, but the kill test itself is still worth running by hand.
