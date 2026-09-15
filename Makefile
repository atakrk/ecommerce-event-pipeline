PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python

.PHONY: venv reference reference-force events clean-data

venv: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt
	touch $@

# Skips files that already exist — reference data is generated once.
reference: venv
	$(PY) -m reference.generate_users
	$(PY) -m reference.generate_products

reference-force: venv
	$(PY) -m reference.generate_users --force
	$(PY) -m reference.generate_products --force

# Events go to stdout, so the target is just a redirect.
# Pass generator options through, e.g. `make events ARGS="--sessions 5000"`.
events: venv
	$(PY) -m generator.run $(ARGS) > data/events.jsonl

clean-data:
	rm -f data/*.jsonl
