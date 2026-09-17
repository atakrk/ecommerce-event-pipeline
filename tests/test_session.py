"""Tests for funnel config validation and single session generation.

uv run pytest tests/test_session.py
"""

import random
from datetime import UTC, datetime

import pytest

from common import ConfigError
from generator.session import EVENT_TYPES, FIRST_EVENT_TYPE, RATE_KEYS, build_session, funnel_rates

START = datetime(2026, 9, 1, tzinfo=UTC)
USER_IDS = ("U000001", "U000002")
PRODUCT_IDS = ("P00001", "P00002", "P00003")


def rates_of(value):
    return dict.fromkeys(RATE_KEYS, value)


def test_funnel_rates_reads_every_step_as_float():
    assert funnel_rates({"funnel": rates_of(1)}) == rates_of(1.0)


@pytest.mark.parametrize("bad", [True, "0.5", -0.1, 1.5, None])
def test_funnel_rates_rejects_values_that_are_not_probabilities(bad):
    funnel = rates_of(0.5) | {RATE_KEYS[0]: bad}
    with pytest.raises(ConfigError, match=RATE_KEYS[0]):
        funnel_rates({"funnel": funnel})


def test_funnel_rates_names_the_missing_key():
    funnel = rates_of(0.5)
    del funnel[RATE_KEYS[-1]]
    with pytest.raises(ConfigError, match=RATE_KEYS[-1]):
        funnel_rates({"funnel": funnel})


def session(seed, rate):
    return build_session(random.Random(seed), START, USER_IDS, PRODUCT_IDS, rates_of(rate))


def test_rate_zero_stops_after_the_page_view():
    events = session("s", 0.0)
    assert [event["event_type"] for event in events] == [FIRST_EVENT_TYPE]
    assert events[0]["product_id"] is None


def test_rate_one_walks_the_whole_funnel_with_one_product():
    events = session("s", 1.0)
    assert tuple(event["event_type"] for event in events) == EVENT_TYPES
    assert len({event["product_id"] for event in events[1:]}) == 1
    assert len({event["session_id"] for event in events}) == 1
    assert len({event["user_id"] for event in events}) == 1


def test_event_times_strictly_increase():
    times = [event["event_time"] for event in session("s", 1.0)]
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_same_seed_gives_the_same_session():
    assert session("same", 0.5) == session("same", 0.5)
