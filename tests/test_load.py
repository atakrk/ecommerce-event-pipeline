"""Tests for the DuckDB loader: path precedence, hashing, and real loads into a temp warehouse.

uv run pytest tests/test_load.py
"""

import hashlib
import json
import subprocess
import sys
import textwrap

import duckdb
import pytest
import yaml

from common import PROJECT_ROOT, ConfigError, write_json, write_jsonl
from pipeline.load import (
    check_inputs,
    describe,
    flat_value,
    flatten,
    resolve_database,
    resolve_inputs,
    resolve_manifest,
    sha256_of,
)
from pipeline.load import main as load_main

USERS = [{"user_id": "U000001", "country": "TR", "city": "Istanbul"}]
PRODUCTS = [{"product_id": "P00001", "category": "fashion", "price": 12.5}]
EVENTS = [
    {"event_id": "E1", "user_id": "U000001", "product_id": None, "event_type": "PAGE_VIEW"},
    {"event_id": "E2", "user_id": "U000001", "product_id": "P00001", "event_type": "PRODUCT_VIEW"},
]


# Fixtures: a small dataset plus the config that points at it.


@pytest.fixture
def data_dir(tmp_path):
    directory = tmp_path / "data"
    write_jsonl(directory / "users.jsonl", USERS)
    write_jsonl(directory / "products.jsonl", PRODUCTS)
    write_jsonl(directory / "events.jsonl", EVENTS)
    return directory


@pytest.fixture
def config_path(tmp_path, data_dir):
    path = tmp_path / "config.yaml"
    config = {"seed": 42, "paths": {"data_dir": str(data_dir)}, "funnel": {"a_to_b": 0.5}}
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def warehouse(tmp_path):
    return tmp_path / "warehouse.duckdb"


def load(config_path, warehouse, *extra):
    return load_main(["--config", str(config_path), "--database", str(warehouse), *extra])


def query(warehouse, sql, parameters=None):
    connection = duckdb.connect(str(warehouse))
    try:
        return connection.execute(sql, parameters or []).fetchall()
    finally:
        connection.close()


# resolve_database / resolve_inputs / resolve_manifest


def test_the_database_flag_wins_over_everything():
    config = {"paths": {"warehouse": "from/config.duckdb"}}
    env = {"DBT_DUCKDB_PATH": "/from/env.duckdb"}
    assert resolve_database("/from/flag.duckdb", config, env).as_posix() == "/from/flag.duckdb"


def test_the_environment_wins_over_the_config():
    config = {"paths": {"warehouse": "from/config.duckdb"}}
    env = {"DBT_DUCKDB_PATH": "/from/env.duckdb"}
    assert resolve_database(None, config, env).as_posix() == "/from/env.duckdb"


def test_a_relative_config_path_hangs_off_the_repo_root():
    config = {"paths": {"warehouse": "data/other.duckdb"}}
    assert resolve_database(None, config, {}) == PROJECT_ROOT / "data" / "other.duckdb"


def test_the_default_warehouse_is_used_when_nothing_else_is_set():
    assert resolve_database(None, {}, {}) == PROJECT_ROOT / "data" / "warehouse.duckdb"


def test_the_expected_three_files_come_from_the_data_dir(data_dir):
    config = {"paths": {"data_dir": str(data_dir)}}
    assert resolve_inputs(config, []) == (
        ("users", data_dir / "users.jsonl"),
        ("products", data_dir / "products.jsonl"),
        ("events", data_dir / "events.jsonl"),
    )


def test_positional_paths_replace_the_defaults_and_name_their_kind(data_dir):
    config = {"paths": {"data_dir": str(data_dir)}}
    inputs = resolve_inputs(config, [data_dir / "events.jsonl"])
    assert inputs == (("events", data_dir / "events.jsonl"),)


def test_the_manifest_defaults_to_the_data_dir(data_dir):
    config = {"paths": {"data_dir": str(data_dir)}}
    assert resolve_manifest(config, None) == data_dir / "events.manifest.json"


# check_inputs


def test_a_missing_input_file_is_named(tmp_path):
    missing = tmp_path / "users.jsonl"
    with pytest.raises(ConfigError, match=f"{missing} is missing or unreadable"):
        check_inputs([("users", missing)])


def test_a_file_name_that_is_not_a_table_name_is_rejected(tmp_path):
    path = tmp_path / "Events 2024.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="which is not a table name"):
        check_inputs([(path.stem, path)])


# sha256_of / flatten / describe


def test_the_hash_is_the_sha256_of_the_bytes(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    assert sha256_of(path) == hashlib.sha256(b'{"a": 1}\n').hexdigest()


def test_flatten_turns_nesting_into_dotted_keys():
    assert flatten({"paths": {"data_dir": "data"}, "seed": 42}) == (
        ("paths.data_dir", "data"),
        ("seed", "42"),
    )


def test_a_list_leaf_keeps_its_json_shape():
    assert flatten({"cities": ["Istanbul", "Ankara"]}) == (("cities", '["Istanbul", "Ankara"]'),)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (True, "True"), (0.6, "0.6"), ("text", "text"), ({}, "{}")],
)
def test_flat_value_renders_each_leaf_kind(value, expected):
    assert flat_value(value) == expected


def test_describe_distinguishes_skipped_rows_and_the_manifest():
    assert describe("events", "skipped_duplicate", 0) == "skipped, already loaded"
    assert describe("events", "loaded", 12) == "12 lines"
    assert describe("manifest", "loaded", 0) == "settings recorded"


# End to end: a real load into a temporary warehouse


def test_a_first_load_writes_every_line_with_its_own_line_number(config_path, warehouse, data_dir):
    assert load(config_path, warehouse) == 0

    rows = query(warehouse, "select line_number, raw_line, source_file from raw.events order by 1")
    assert [line_number for line_number, _, _ in rows] == [1, 2]
    assert json.loads(rows[0][1])["event_id"] == "E1"
    assert rows[0][2] == str(data_dir / "events.jsonl")
    assert query(warehouse, "select count(*) from raw.users") == [(1,)]
    assert query(warehouse, "select status, total_rows from meta.load_runs") == [("succeeded", 4)]


def test_line_numbers_follow_the_file_even_across_blank_lines(config_path, warehouse, data_dir):
    (data_dir / "events.jsonl").write_text('{"event_id": "E1"}\n\n{"event_id": "E2"}\r\n', "utf-8")

    load(config_path, warehouse)

    rows = query(warehouse, "select line_number, raw_line from raw.events order by 1")
    # Line 2 is blank and is not stored; line 3 keeps its real number and loses the \r.
    assert rows == [(1, '{"event_id": "E1"}'), (3, '{"event_id": "E2"}')]


def test_a_second_run_loads_nothing_but_still_records_itself(config_path, warehouse):
    load(config_path, warehouse)
    assert load(config_path, warehouse) == 0

    assert query(warehouse, "select count(*) from raw.events") == [(2,)]
    assert query(warehouse, "select status, total_rows from meta.load_runs order by load_id") == [
        ("succeeded", 4),
        ("succeeded", 0),
    ]
    statuses = query(warehouse, "select distinct status from meta.load_files where load_id = 2")
    assert statuses == [("skipped_duplicate",)]


def test_force_loads_a_file_that_was_already_loaded(config_path, warehouse):
    load(config_path, warehouse)
    assert load(config_path, warehouse, "--force") == 0

    assert query(warehouse, "select load_id, count(*) from raw.events group by 1 order by 1") == [
        (1, 2),
        (2, 2),
    ]


def test_changed_contents_are_loaded_again_without_force(config_path, warehouse, data_dir):
    load(config_path, warehouse)
    write_jsonl(data_dir / "events.jsonl", [*EVENTS, {"event_id": "E3"}])

    load(config_path, warehouse)

    assert query(warehouse, "select count(*) from raw.events") == [(5,)]


def test_a_missing_input_aborts_before_the_warehouse_is_touched(config_path, warehouse, data_dir):
    (data_dir / "products.jsonl").unlink()

    exit_code = load(config_path, warehouse)

    assert exit_code == 2
    assert not warehouse.exists()


def test_a_missing_manifest_warns_but_still_loads(config_path, warehouse, capsys):
    assert load(config_path, warehouse) == 0

    assert "warning: no manifest at" in capsys.readouterr().err
    sources = query(warehouse, "select distinct source from meta.run_config")
    assert sources == [("config",)]
    assert query(warehouse, "select value from meta.run_config where key = 'seed'") == [("42",)]


def test_a_manifest_is_stored_beside_the_config_settings(config_path, warehouse, data_dir):
    write_json(data_dir / "events.manifest.json", {"seed": 42, "funnel": {"a_to_b": 0.5}})

    load(config_path, warehouse)

    rows = query(
        warehouse, "select key, value from meta.run_config where source = 'manifest' order by key"
    )
    assert rows == [("funnel.a_to_b", "0.5"), ("seed", "42")]
    assert query(warehouse, "select status from meta.load_files where file_kind = 'manifest'") == [
        ("loaded",)
    ]


def test_a_failed_run_is_recorded_and_leaves_no_data(config_path, warehouse, monkeypatch):
    import pipeline.load as loader

    def explode(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(loader, "record_config", explode)

    assert load(config_path, warehouse) == 1

    assert query(warehouse, "select status, error_message from meta.load_runs") == [
        ("failed", "disk on fire")
    ]
    assert query(warehouse, "select count(*) from raw.events") == [(0,)]
    assert query(warehouse, "select count(*) from meta.load_files") == [(0,)]


def test_the_loader_writes_data_to_stderr_only(config_path, warehouse, capsys):
    load(config_path, warehouse)

    out, err = capsys.readouterr()
    assert out == ""
    lines = [line for line in err.splitlines() if not line.startswith("warning:")]
    assert len(lines) == 4  # one per input file, then the total
    assert lines[-1] == "total 4 rows   load_id 1"


def test_a_warehouse_held_by_another_process_names_the_file(config_path, warehouse, capsys):
    load(config_path, warehouse)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(f"""
                import duckdb, time
                connection = duckdb.connect({str(warehouse)!r})
                connection.execute("select 1").fetchall()
                print("held", flush=True)
                time.sleep(30)
            """),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        holder.stdout.readline()
        exit_code = load(config_path, warehouse)
    finally:
        holder.kill()
        holder.wait()

    assert exit_code == 2
    error = capsys.readouterr().err
    assert str(warehouse) in error
    assert "Close the DuckDB MCP server or the DuckDB UI" in error
