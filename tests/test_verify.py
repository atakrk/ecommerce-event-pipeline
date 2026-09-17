"""Tests for the structural checks in verify.py.

uv run pytest tests/test_verify.py
"""

from verify import check_ordering, check_schema, check_sessions, check_unique_event_ids


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
