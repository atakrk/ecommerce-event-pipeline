"""Check a generated event file against the configured funnel.

    python verify.py [data/events.jsonl]

The funnel percentages are a report, not a verdict: they drift with sample size,
which is the point of measuring them. The structural checks below are pass/fail,
and any failure exits non-zero.
"""
import sys
from collections import Counter, defaultdict

from common import (
    ConfigError,
    base_arg_parser,
    load_config,
    output_path,
    parse_instant,
    read_jsonl,
)
from generator.session import EVENT_TYPES, FIRST_EVENT_TYPE, FUNNEL_STEPS, funnel_rates

DEFAULT_EVENTS_FILE = "events.jsonl"
REQUIRED_FIELDS = ("event_id", "session_id", "user_id", "event_type", "event_time", "product_id")

# A broken file can fail a check on every line; showing the first few is enough
# to identify the problem without burying the report.
MAX_REPORTED = 5

ROW = "{:<28}{:>11}{:>11}{:>9}"


def parse_args(argv=None):
    parser = base_arg_parser(__doc__)
    parser.add_argument(
        "path", nargs="?", default=None, help="Event file to check (default: data/events.jsonl)"
    )
    return parser.parse_args(argv)


def check_schema(events):
    """Validate the fields every later check depends on, and attach the parsed time.

    Events that fail are reported and then left out, so one malformed line does
    not cascade into a misleading failure in every other check.
    """
    failures = []
    usable = []
    for line_number, event in enumerate(events, start=1):
        missing = [field for field in REQUIRED_FIELDS if field not in event]
        if missing:
            failures.append(f"line {line_number}: missing field(s) {', '.join(missing)}")
            continue
        if event["event_type"] not in EVENT_TYPES:
            failures.append(f"line {line_number}: unknown event_type {event['event_type']!r}")
            continue
        try:
            moment = parse_instant(event["event_time"])
        except ValueError:
            failures.append(f"line {line_number}: unparseable event_time {event['event_time']!r}")
            continue
        usable.append((moment, event))
    return failures, usable


def check_unique_event_ids(usable):
    counts = Counter(event["event_id"] for _, event in usable)
    return [
        f"event_id {event_id} appears {count} times"
        for event_id, count in counts.most_common()
        if count > 1
    ]


def check_ordering(usable):
    """The file must be chronological, exactly as a real stream arrives."""
    failures = []
    for line_number, (earlier, later) in enumerate(zip(usable, usable[1:]), start=2):
        if later[0] < earlier[0]:
            failures.append(
                f"line {line_number}: {later[1]['event_time']} follows {earlier[1]['event_time']}"
            )
    return failures


def check_sessions(usable):
    """Per-session structure: one PAGE_VIEW first, no skipped stage, one product."""
    sessions = defaultdict(list)
    for moment, event in usable:
        sessions[event["session_id"]].append((moment, event))

    page_view_failures = []
    stage_failures = []
    product_failures = []

    for session_id, entries in sessions.items():
        entries.sort(key=lambda entry: entry[0])
        types = [event["event_type"] for _, event in entries]

        page_views = types.count(FIRST_EVENT_TYPE)
        if page_views != 1:
            page_view_failures.append(f"session {session_id} has {page_views} {FIRST_EVENT_TYPE} events")
        elif types[0] != FIRST_EVENT_TYPE:
            page_view_failures.append(
                f"session {session_id} starts with {types[0]}, not {FIRST_EVENT_TYPE}"
            )

        expected = list(EVENT_TYPES[: len(types)])
        if types != expected:
            stage_failures.append(
                f"session {session_id} is {' -> '.join(types)}, expected {' -> '.join(expected)}"
            )

        products = {event["product_id"] for _, event in entries if event["event_type"] != FIRST_EVENT_TYPE}
        if None in products:
            product_failures.append(f"session {session_id} has an event with no product_id")
        elif len(products) > 1:
            product_failures.append(
                f"session {session_id} carries {len(products)} products: {', '.join(sorted(products))}"
            )

    return len(sessions), page_view_failures, stage_failures, product_failures


def print_funnel(usable, rates, stream):
    """Observed rate per stage: sessions reaching the stage over sessions reaching the one before.

    Counted over distinct sessions rather than events, so a structurally broken
    file still yields a readable report instead of an inflated one.
    """
    reached = defaultdict(set)
    for _, event in usable:
        reached[event["event_type"]].add(event["session_id"])

    print(ROW.format("stage", "configured", "observed", "diff"), file=stream)
    previous = FIRST_EVENT_TYPE
    for event_type, rate_key in FUNNEL_STEPS:
        configured = rates[rate_key]
        denominator = len(reached[previous])
        if denominator:
            observed = len(reached[event_type]) / denominator
            observed_text = f"{observed:.1%}"
            diff_text = f"{(observed - configured) * 100:+.1f}"
        else:
            observed_text = diff_text = "n/a"
        print(
            ROW.format(rate_key, f"{configured:.1%}", observed_text, diff_text),
            file=stream,
        )
        previous = event_type


def report_failures(named_failures, stream):
    total = 0
    for name, failures in named_failures:
        if not failures:
            continue
        total += len(failures)
        print(f"FAILED  {name} ({len(failures)})", file=stream)
        for failure in failures[:MAX_REPORTED]:
            print(f"  {failure}", file=stream)
        if len(failures) > MAX_REPORTED:
            print(f"  ... and {len(failures) - MAX_REPORTED} more", file=stream)
    return total


def main(argv=None):
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        rates = funnel_rates(config)
    except (OSError, ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    path = args.path if args.path is not None else output_path(config, DEFAULT_EVENTS_FILE)
    try:
        events = list(read_jsonl(path))
    except (OSError, ValueError) as error:
        print(f"error: cannot read {path}: {error}", file=sys.stderr)
        return 2
    if not events:
        print(f"error: {path} contains no events", file=sys.stderr)
        return 2

    schema_failures, usable = check_schema(events)
    session_count, page_view_failures, stage_failures, product_failures = check_sessions(usable)
    ordering_failures = check_ordering(usable)

    print_funnel(usable, rates, sys.stdout)
    print(file=sys.stdout)
    print(
        f"sessions {session_count}   events {len(events)}"
        f"   ordered by event_time: {'no' if ordering_failures else 'yes'}",
        file=sys.stdout,
    )

    # Both streams usually land on the same terminal; flush so the report does
    # not appear after the failures it belongs to.
    sys.stdout.flush()
    failed = report_failures(
        [
            ("event schema", schema_failures),
            ("one PAGE_VIEW, first in the session", page_view_failures),
            ("no skipped funnel stage", stage_failures),
            ("one product per session", product_failures),
            ("unique event_id", check_unique_event_ids(usable)),
            ("sorted by event_time", ordering_failures),
        ],
        sys.stderr,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
