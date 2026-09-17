RUN := uv run
DBT := $(RUN) dbt
DBT_ARGS := --project-dir transform --profiles-dir transform

# Absolute path, so dbt opens this warehouse from any working directory (spec 0001).
export DBT_DUCKDB_PATH := $(CURDIR)/data/warehouse.duckdb

# Sessions for the small seeded dataset `make check` and CI generate.
CHECK_SESSIONS ?= 2000

.PHONY: sync reference reference-force events verify load pipeline dbt-deps dbt-build \
	lint test check clean-data clean-warehouse

# Installs Python 3.12 and the locked dependencies into .venv.
sync:
	uv sync --locked

# Skips files that already exist — reference data is generated once.
reference:
	$(RUN) python -m reference.generate_users
	$(RUN) python -m reference.generate_products

reference-force:
	$(RUN) python -m reference.generate_users --force
	$(RUN) python -m reference.generate_products --force

# Events go to stdout, so the target is just a redirect. The manifest records the
# settings this run used, and the loader stores it alongside the events.
# Pass generator options through, e.g. `make events ARGS="--sessions 5000"`.
events:
	$(RUN) python -m generator.run --manifest data/events.manifest.json $(ARGS) > data/events.jsonl

# Reports the observed funnel and fails on any structural problem.
verify:
	$(RUN) python verify.py $(ARGS)

# Loads the generated files into raw.* — a file already loaded is skipped, so this is
# safe to repeat. Pass loader options through, e.g. `make load ARGS="--force"`.
load:
	$(RUN) python -m pipeline.load $(ARGS)

# Generated files to queryable marts, in one command. Two nested makes rather than
# prerequisites, so the order holds even under `make -j`.
pipeline:
	$(MAKE) load
	$(MAKE) dbt-build

dbt-deps:
	$(DBT) deps $(DBT_ARGS)

# Close any other process holding data/warehouse.duckdb (MCP server, DuckDB UI) first.
dbt-build: dbt-deps
	$(DBT) build $(DBT_ARGS)

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .

test:
	$(RUN) pytest

# Everything CI runs: lint, tests, then the whole pipeline over a small seeded dataset.
# Both the data and the warehouse live in a temporary folder, so data/ stays untouched.
# The temp warehouse only survives while this one shell line runs, so the dbt build has
# to happen inside it. `$(MAKE) dbt-build DBT_DUCKDB_PATH=...` overrides the path as a
# command line variable: an environment variable would lose to the `:=` export above.
check: lint test
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	sed "s#^  data_dir: .*#  data_dir: $$tmp#" config.yaml > $$tmp/config.yaml && \
	$(RUN) python -c "import sys, common; \
		sys.exit(0 if common.load_config(sys.argv[1])['paths']['data_dir'] == sys.argv[2] \
		else 'make check: could not point paths.data_dir at the temp folder; refusing to write data/')" \
		$$tmp/config.yaml $$tmp && \
	$(RUN) python -m reference.generate_users --config $$tmp/config.yaml > /dev/null && \
	$(RUN) python -m reference.generate_products --config $$tmp/config.yaml > /dev/null && \
	$(RUN) python -m generator.run --config $$tmp/config.yaml --sessions $(CHECK_SESSIONS) \
		--manifest $$tmp/events.manifest.json > $$tmp/events.jsonl && \
	$(RUN) python verify.py --config $$tmp/config.yaml && \
	$(RUN) python -m pipeline.load --config $$tmp/config.yaml --database $$tmp/warehouse.duckdb && \
	$(MAKE) dbt-build DBT_DUCKDB_PATH=$$tmp/warehouse.duckdb

clean-data:
	rm -f data/*.jsonl data/events.manifest.json

# Deletes all load history on purpose; it cannot be rebuilt from the JSONL files alone.
clean-warehouse:
	rm -f data/warehouse.duckdb data/warehouse.duckdb.wal
