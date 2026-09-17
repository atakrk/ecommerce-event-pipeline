"""Tests for the shared helpers in common.py.

uv run pytest tests/test_common.py
"""

from datetime import UTC, datetime

import pytest

from common import format_instant, parse_instant, read_jsonl, rng_for, write_jsonl


def test_parse_instant_accepts_z_suffix():
    assert parse_instant("2026-09-01T00:00:00Z") == datetime(2026, 9, 1, tzinfo=UTC)


def test_parse_instant_treats_missing_offset_as_utc():
    assert parse_instant("2026-09-01T12:30:00") == datetime(2026, 9, 1, 12, 30, tzinfo=UTC)


def test_parse_instant_converts_other_offsets_to_utc():
    assert parse_instant("2026-09-01T03:00:00+03:00") == datetime(2026, 9, 1, tzinfo=UTC)


def test_parse_instant_strips_surrounding_whitespace():
    assert parse_instant(" 2026-09-01T00:00:00Z\n") == datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.parametrize("value", ["", "yesterday", "2026-13-01T00:00:00Z"])
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
