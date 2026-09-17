"""Tests for the event generator CLI: settings validation, generation, and exit codes.

uv run pytest tests/test_run.py
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import yaml

from common import ConfigError, write_jsonl
from generator.run import generate_events, main, resolve_settings

FUNNEL = {
    "page_view_to_product_view": 0.6,
    "product_view_to_add_to_cart": 0.5,
    "add_to_cart_to_checkout": 0.5,
    "checkout_to_purchase": 0.5,
}
USER_IDS = [f"U{i:06d}" for i in range(1, 21)]
PRODUCT_IDS = [f"P{i:05d}" for i in range(1, 11)]


def config_with(**overrides):
    config = {
        "seed": 42,
        "paths": {"data_dir": "data"},
        "simulation": {"start": "2026-09-01T00:00:00Z", "window_hours": 24},
        "funnel": dict(FUNNEL),
    }
    config.update(overrides)
    return config


def args_with(sessions=10, start=None, seed=None):
    return SimpleNamespace(sessions=sessions, start=start, seed=seed)


def events_for(config, sessions):
    settings = resolve_settings(config, args_with(sessions=sessions))
    return generate_events(config, settings, USER_IDS, PRODUCT_IDS)


# resolve_settings


def test_settings_come_from_the_config_by_default():
    settings = resolve_settings(config_with(), args_with(sessions=5))
    assert settings.sessions == 5
    assert settings.start == datetime(2026, 9, 1, tzinfo=UTC)
    assert settings.window_seconds == 24 * 3600
    assert settings.rates == FUNNEL


def test_start_flag_overrides_the_config():
    settings = resolve_settings(config_with(), args_with(start="2026-10-01T06:00:00Z"))
    assert settings.start == datetime(2026, 10, 1, 6, tzinfo=UTC)


def test_seed_flag_replaces_the_config_seed():
    config = config_with()
    resolve_settings(config, args_with(seed=7))
    assert config["seed"] == 7


@pytest.mark.parametrize("sessions", [0, -3])
def test_sessions_below_one_are_rejected(sessions):
    with pytest.raises(ConfigError, match="--sessions must be at least 1"):
        resolve_settings(config_with(), args_with(sessions=sessions))


def test_a_missing_start_names_both_places_to_set_it():
    with pytest.raises(ConfigError, match="pass --start or set simulation.start"):
        resolve_settings(config_with(simulation={}), args_with())


@pytest.mark.parametrize(
    ("start_flag", "config_start", "source"),
    [
        ("not a time", "2026-09-01T00:00:00Z", "--start"),
        (None, "someday", "simulation.start"),
        ("2026-09-01T00:00:00z", "2026-09-01T00:00:00Z", "--start"),
    ],
)
def test_an_unparseable_start_names_its_source(start_flag, config_start, source):
    config = config_with(simulation={"start": config_start})
    with pytest.raises(ConfigError, match=f"^{source} must be an ISO 8601 UTC instant") as error:
        resolve_settings(config, args_with(start=start_flag))
    # Raised `from None`, so the report shows one clear error, not a chained ValueError.
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize("window_hours", [True, "24", None, [24]])
def test_window_hours_must_be_a_number(window_hours):
    config = config_with(simulation={"start": "2026-09-01T00:00:00Z", "window_hours": window_hours})
    with pytest.raises(ConfigError, match="simulation.window_hours must be a positive number"):
        resolve_settings(config, args_with())


@pytest.mark.parametrize("window_hours", [0, -1, 0.0001])
def test_window_hours_must_cover_at_least_one_second(window_hours):
    config = config_with(simulation={"start": "2026-09-01T00:00:00Z", "window_hours": window_hours})
    with pytest.raises(ConfigError, match="must cover at least one second"):
        resolve_settings(config, args_with())


def test_a_bad_funnel_rate_fails_before_anything_else():
    config = config_with(funnel=dict(FUNNEL, checkout_to_purchase=2))
    with pytest.raises(ConfigError, match="funnel.checkout_to_purchase"):
        resolve_settings(config, args_with(sessions=0))


# generate_events


def test_events_are_sorted_by_event_time_and_stay_in_the_window():
    events = events_for(config_with(), sessions=50)
    times = [event["event_time"] for event in events]
    assert times == sorted(times)
    assert times[0] >= "2026-09-01T00:00:00Z"
    # The last session can start at the end of the window; four gaps of at most 120s follow.
    assert times[-1] <= "2026-09-02T00:08:00Z"


def test_every_session_starts_with_one_page_view():
    events = events_for(config_with(), sessions=50)
    page_views = [event for event in events if event["event_type"] == "PAGE_VIEW"]
    assert len(page_views) == 50
    assert len({event["session_id"] for event in events}) == 50


def test_raising_sessions_keeps_the_earlier_sessions_identical():
    smaller = events_for(config_with(), sessions=30)
    larger = events_for(config_with(), sessions=40)
    as_lines = {json.dumps(event, sort_keys=True) for event in larger}
    assert all(json.dumps(event, sort_keys=True) in as_lines for event in smaller)


def test_a_different_seed_gives_different_events():
    assert events_for(config_with(), 20) != events_for(config_with(seed=43), 20)


# main (the CLI edge)


def write_reference(data_dir, users=USER_IDS, products=PRODUCT_IDS):
    write_jsonl(data_dir / "users.jsonl", [{"user_id": user_id} for user_id in users])
    write_jsonl(data_dir / "products.jsonl", [{"product_id": pid} for pid in products])


def write_config(tmp_path, **overrides):
    config = config_with(paths={"data_dir": str(tmp_path)}, **overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return str(path)


def test_main_writes_events_to_stdout_and_the_summary_to_stderr(tmp_path, capsys):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)

    exit_code = main(["--config", config_path, "--sessions", "5"])

    out, err = capsys.readouterr()
    events = [json.loads(line) for line in out.splitlines()]
    assert exit_code == 0
    assert len([event for event in events if event["event_type"] == "PAGE_VIEW"]) == 5
    assert err.startswith(f"sessions 5   events {len(events)}")


def test_main_is_byte_identical_across_runs(tmp_path, capsys):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)

    main(["--config", config_path, "--sessions", "25"])
    first = capsys.readouterr().out
    main(["--config", config_path, "--sessions", "25"])
    second = capsys.readouterr().out

    assert first == second


def test_the_manifest_describes_the_run_that_produced_the_events(tmp_path, capsys):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)
    manifest_path = tmp_path / "events.manifest.json"

    main(["--config", config_path, "--sessions", "12", "--manifest", str(manifest_path)])

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["seed"] == 42
    assert manifest["sessions"] == 12
    assert manifest["start"] == "2026-09-01T00:00:00Z"
    assert manifest["window_hours"] == 24
    assert manifest["funnel"] == FUNNEL
    assert manifest["sessions_written"] == 12
    assert manifest["events_written"] == len(events)


def test_the_manifest_is_byte_identical_across_runs(tmp_path):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    main(["--config", config_path, "--sessions", "20", "--manifest", str(first)])
    main(["--config", config_path, "--sessions", "20", "--manifest", str(second)])

    assert first.read_bytes() == second.read_bytes()


def test_no_manifest_is_written_unless_the_option_is_passed(tmp_path):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)

    main(["--config", config_path, "--sessions", "5"])

    assert not (tmp_path / "events.manifest.json").exists()


def test_an_unwritable_manifest_path_fails_before_any_events_are_written(tmp_path, capsys):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)
    unwritable = tmp_path / "events.jsonl" / "manifest.json"
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")

    exit_code = main(["--config", config_path, "--sessions", "5", "--manifest", str(unwritable)])

    out, err = capsys.readouterr()
    assert exit_code == 2
    assert out == ""
    assert f"cannot write the manifest to {unwritable}" in err


def test_main_exits_2_when_reference_data_is_missing(tmp_path, capsys):
    config_path = write_config(tmp_path)

    exit_code = main(["--config", config_path])

    out, err = capsys.readouterr()
    assert exit_code == 2
    assert out == ""
    assert "users.jsonl is missing -- run `make reference` first" in err


def test_main_exits_2_when_a_reference_file_is_empty(tmp_path, capsys):
    write_reference(tmp_path, products=[])
    config_path = write_config(tmp_path)

    exit_code = main(["--config", config_path])

    out, err = capsys.readouterr()
    assert exit_code == 2
    assert out == ""
    assert "products.jsonl is empty" in err


def test_main_exits_2_on_an_invalid_flag_before_any_output(tmp_path, capsys):
    write_reference(tmp_path)
    config_path = write_config(tmp_path)

    exit_code = main(["--config", config_path, "--start", "tomorrow"])

    out, err = capsys.readouterr()
    assert exit_code == 2
    assert out == ""
    assert err == "error: --start must be an ISO 8601 UTC instant, got 'tomorrow'\n"
