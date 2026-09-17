"""Tests for the structural checks in verify.py.

uv run pytest tests/test_verify.py
"""

import io
import json

from verify import (
    check_ordering,
    check_schema,
    check_sessions,
    check_unique_event_ids,
    main,
    print_funnel,
)


def event(event_type, event_time, session_id="s1", event_id=None, product_id="P00001"):
    return {
        "event_id": event_id or f"{session_id}-{event_type}",
        "session_id": session_id,
        "user_id": "U000001",
        "event_type": event_type,
        "event_time": event_time,
        "product_id": None if event_type == "PAGE_VIEW" else product_id,
    }


def full_session():
    return [
        event("PAGE_VIEW", "2026-09-01T00:00:00Z"),
        event("PRODUCT_VIEW", "2026-09-01T00:00:10Z"),
        event("ADD_TO_CART", "2026-09-01T00:00:20Z"),
    ]


def usable(events):
    failures, rows = check_schema(events)
    assert failures == []
    return rows


def test_a_well_formed_session_passes_every_check():
    rows = usable(full_session())
    assert check_ordering(rows) == []
    assert check_unique_event_ids(rows) == []
    assert check_sessions(rows) == (1, [], [], [])


def test_schema_reports_missing_fields_unknown_types_and_bad_times():
    missing = {"event_id": "x"}
    unknown = event("REFUND", "2026-09-01T00:00:00Z")
    bad_time = event("PAGE_VIEW", "not a time")

    failures, rows = check_schema([missing, unknown, bad_time])

    assert rows == []
    assert failures[0].startswith("line 1: missing field(s)")
    assert failures[1] == "line 2: unknown event_type 'REFUND'"
    assert failures[2] == "line 3: unparseable event_time 'not a time'"


def test_ordering_names_the_line_that_goes_back_in_time():
    events = full_session()
    events[1], events[2] = events[2], events[1]
    assert check_ordering(usable(events)) == [
        "line 3: 2026-09-01T00:00:10Z follows 2026-09-01T00:00:20Z"
    ]


def test_duplicate_event_ids_are_reported():
    events = full_session()
    events[2]["event_id"] = events[1]["event_id"]
    assert check_unique_event_ids(usable(events)) == ["event_id s1-PRODUCT_VIEW appears 2 times"]


def test_sessions_report_skipped_stages_and_mixed_products():
    events = [
        event("PAGE_VIEW", "2026-09-01T00:00:00Z"),
        event("ADD_TO_CART", "2026-09-01T00:00:10Z", product_id="P00001"),
        event("CHECKOUT", "2026-09-01T00:00:20Z", product_id="P00002"),
    ]
    count, page_views, stages, products = check_sessions(usable(events))
    assert count == 1
    assert page_views == []
    assert len(stages) == 1 and "PAGE_VIEW -> ADD_TO_CART -> CHECKOUT" in stages[0]
    assert products == ["session s1 carries 2 products: P00001, P00002"]


def test_funnel_report_shows_n_a_when_no_session_reached_the_previous_stage():
    rows = usable([event("PAGE_VIEW", "2026-09-01T00:00:00Z")])
    rates = {
        "page_view_to_product_view": 0.6,
        "product_view_to_add_to_cart": 0.15,
        "add_to_cart_to_checkout": 0.5,
        "checkout_to_purchase": 0.6,
    }
    stream = io.StringIO()

    print_funnel(rows, rates, stream)

    lines = stream.getvalue().splitlines()
    assert lines[1].split() == ["page_view_to_product_view", "60.0%", "0.0%", "-60.0"]
    assert lines[2].split() == ["product_view_to_add_to_cart", "15.0%", "n/a", "n/a"]


def write_events(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return str(path)


def test_main_exits_0_and_reports_on_stdout_for_a_clean_file(tmp_path, capsys):
    path = write_events(tmp_path / "events.jsonl", full_session())

    exit_code = main([path])

    out, err = capsys.readouterr()
    assert exit_code == 0
    assert "sessions 1   events 3   ordered by event_time: yes" in out
    assert err == ""


def test_main_exits_1_and_lists_failures_on_stderr(tmp_path, capsys):
    events = full_session()
    events.append(dict(events[0], event_id="dup"))
    events.append(dict(events[1], event_id="dup"))
    path = write_events(tmp_path / "events.jsonl", events)

    exit_code = main([path])

    out, err = capsys.readouterr()
    assert exit_code == 1
    assert "ordered by event_time: no" in out
    assert "FAILED  unique event_id (1)" in err
    assert "FAILED  sorted by event_time" in err


def test_main_caps_each_failure_list(tmp_path, capsys):
    events = [dict(event("PAGE_VIEW", "2026-09-01T00:00:00Z"), event_type="REFUND")] * 8
    path = write_events(tmp_path / "events.jsonl", events)

    main([path])

    err = capsys.readouterr().err
    assert "FAILED  event schema (8)" in err
    assert "  ... and 3 more" in err


def test_main_exits_2_on_an_empty_file(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")

    assert main([str(path)]) == 2
    assert "contains no events" in capsys.readouterr().err


def test_main_exits_2_on_a_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing.jsonl")]) == 2
    assert "error: cannot read" in capsys.readouterr().err


def test_main_exits_2_on_malformed_json(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    path.write_text("{not json\n", encoding="utf-8")

    assert main([str(path)]) == 2
    assert "error: cannot read" in capsys.readouterr().err
