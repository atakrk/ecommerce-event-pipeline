"""Tests for the shared helpers in common.py.

uv run pytest tests/test_common.py
"""

import random
from datetime import UTC, datetime

import pytest

from common import (
    ensure_writable,
    format_instant,
    load_config,
    output_path,
    parse_instant,
    read_jsonl,
    rng_for,
    weighted_choice,
    write_jsonl,
)


def test_parse_instant_accepts_z_suffix():
    assert parse_instant("2026-09-01T00:00:00Z") == datetime(2026, 9, 1, tzinfo=UTC)


def test_parse_instant_treats_missing_offset_as_utc():
    assert parse_instant("2026-09-01T12:30:00") == datetime(2026, 9, 1, 12, 30, tzinfo=UTC)


def test_parse_instant_converts_other_offsets_to_utc():
    assert parse_instant("2026-09-01T03:00:00+03:00") == datetime(2026, 9, 1, tzinfo=UTC)


def test_parse_instant_strips_surrounding_whitespace():
    assert parse_instant(" 2026-09-01T00:00:00Z\n") == datetime(2026, 9, 1, tzinfo=UTC)


# The hand written "Z" handling was dropped for Python 3.12's native parser, which
# accepts only an uppercase "Z". Every instant the pipeline writes uses uppercase.
@pytest.mark.parametrize("value", ["", "yesterday", "2026-13-01T00:00:00Z", "2026-09-01T00:00:00z"])
def test_parse_instant_rejects_unparseable_values(value):
    with pytest.raises(ValueError):
        parse_instant(value)


def test_format_instant_round_trips_through_parse():
    text = "2026-09-01T08:15:42Z"
    assert format_instant(parse_instant(text)) == text


def test_rng_for_is_deterministic_per_seed_and_name():
    config = {"seed": 42}
    assert rng_for(config, "users").random() == rng_for(config, "users").random()


def test_rng_for_keeps_names_independent():
    config = {"seed": 42}
    assert rng_for(config, "users").random() != rng_for(config, "products").random()


def test_write_jsonl_round_trips_and_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "out" / "records.jsonl"
    records = [{"id": 1, "city": "İzmir"}, {"id": 2, "city": None}]

    write_jsonl(path, records)

    assert list(read_jsonl(path)) == records
    assert [p.name for p in path.parent.iterdir()] == ["records.jsonl"]


def test_write_jsonl_removes_tmp_file_on_failure(tmp_path):
    path = tmp_path / "records.jsonl"

    def broken():
        yield {"id": 1}
        raise RuntimeError("generation failed")

    with pytest.raises(RuntimeError):
        write_jsonl(path, broken())

    assert list(tmp_path.iterdir()) == []


def test_ensure_writable_allows_a_new_file(tmp_path):
    assert ensure_writable(tmp_path / "users.jsonl", force=False) is True


def test_ensure_writable_skips_an_existing_file_and_says_so(tmp_path, capsys):
    path = tmp_path / "users.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    assert ensure_writable(path, force=False) is False
    assert "already exists, skipping" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_ensure_writable_overwrites_an_existing_file_with_force(tmp_path):
    path = tmp_path / "users.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    assert ensure_writable(path, force=True) is True


def test_output_path_resolves_a_relative_data_dir_under_the_project(tmp_path):
    path = output_path({"paths": {"data_dir": "data"}}, "users.jsonl")
    assert path.parts[-2:] == ("data", "users.jsonl")
    assert path.is_absolute()


def test_output_path_keeps_an_absolute_data_dir(tmp_path):
    assert output_path({"paths": {"data_dir": str(tmp_path)}}, "users.jsonl") == (
        tmp_path / "users.jsonl"
    )


def test_load_config_reads_the_project_config():
    config = load_config()
    assert isinstance(config["seed"], int)
    assert set(config) >= {"paths", "users", "products", "simulation", "funnel"}


def test_weighted_choice_never_picks_a_zero_weight_key():
    rng = random.Random(1)
    picks = {weighted_choice(rng, {"never": 0, "always": 1}) for _ in range(200)}
    assert picks == {"always"}


def test_read_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"id": 1}\n\n   \n{"id": 2}\n', encoding="utf-8")
    assert list(read_jsonl(path)) == [{"id": 1}, {"id": 2}]


def test_read_jsonl_raises_on_a_malformed_line(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"id": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError):
        list(read_jsonl(path))
