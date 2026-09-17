RUN := uv run
DBT := $(RUN) dbt
DBT_ARGS := --project-dir transform --profiles-dir transform

# Absolute path, so dbt opens this warehouse from any working directory (spec 0001).
export DBT_DUCKDB_PATH := $(CURDIR)/data/warehouse.duckdb

# Sessions for the small seeded dataset `make check` and CI generate.
CHECK_SESSIONS ?= 2000

.PHONY: sync reference reference-force events verify dbt-deps dbt-build lint test check \
	clean-data clean-warehouse

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

# Events go to stdout, so the target is just a redirect.
# Pass generator options through, e.g. `make events ARGS="--sessions 5000"`.
events:
	$(RUN) python -m generator.run $(ARGS) > data/events.jsonl

# Reports the observed funnel and fails on any structural problem.
verify:
	$(RUN) python verify.py $(ARGS)

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

# Everything CI runs: lint, tests, a small seeded generate + verify, then dbt build.
# The seeded dataset is written to a temporary folder, so your data/ files stay untouched.
check: lint test
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	sed "s#^  data_dir: .*#  data_dir: $$tmp#" config.yaml > $$tmp/config.yaml && \
	$(RUN) python -m reference.generate_users --config $$tmp/config.yaml > /dev/null && \
	$(RUN) python -m reference.generate_products --config $$tmp/config.yaml > /dev/null && \
	$(RUN) python -m generator.run --config $$tmp/config.yaml --sessions $(CHECK_SESSIONS) \
		> $$tmp/events.jsonl && \
	$(RUN) python verify.py --config $$tmp/config.yaml
	$(MAKE) dbt-build

clean-data:
	rm -f data/*.jsonl

# Deletes all load history on purpose; it cannot be rebuilt from the JSONL files alone.
clean-warehouse:
	rm -f data/warehouse.duckdb data/warehouse.duckdb.wal
