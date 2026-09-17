"""Generate session events -> stdout.

Events go to stdout so the stream can be redirected straight into a file:

    python -m generator.run > data/events.jsonl

Everything diagnostic goes to stderr, which keeps the redirected data clean.
"""

import sys
from collections import Counter, namedtuple
from datetime import timedelta
from pathlib import Path

from common import (
    ConfigError,
    base_arg_parser,
    format_instant,
    load_config,
    output_path,
    parse_instant,
    read_jsonl,
    rng_for,
    write_json,
    write_jsonl_stream,
)
from generator.session import EVENT_TYPES, build_session, funnel_rates

DEFAULT_SESSIONS = 1000
DEFAULT_WINDOW_HOURS = 24

Settings = namedtuple("Settings", "sessions start window_seconds rates")


def parse_args(argv=None):
    parser = base_arg_parser(__doc__)
    parser.add_argument(
        "--sessions", type=int, default=DEFAULT_SESSIONS, help="Number of sessions to generate"
    )
    parser.add_argument(
        "--start", help="Start of the arrival window (ISO 8601 UTC); defaults to simulation.start"
    )
    parser.add_argument(
        "--seed", type=int, help="Override the config seed, for one-off experiments"
    )
    parser.add_argument(
        "--manifest", type=Path, help="Also write this run's effective settings to this path"
    )
    return parser.parse_args(argv)


def resolve_settings(config, args):
    """Turn config plus CLI arguments into validated settings.

    Every check happens here, before any event exists, so a bad value is a loud
    failure instead of a plausible-looking file nobody questions.
    """
    if args.seed is not None:
        # The config seed stays the default, so a bare run is reproducible;
        # --seed replaces it for everything derived below.
        config["seed"] = args.seed

    rates = funnel_rates(config)

    if args.sessions < 1:
        raise ConfigError(f"--sessions must be at least 1, got {args.sessions}")

    simulation = config.get("simulation") or {}

    start_value = args.start if args.start is not None else simulation.get("start")
    if start_value is None:
        raise ConfigError("no start instant: pass --start or set simulation.start in the config")
    try:
        start = parse_instant(start_value)
    except ValueError:
        source = "--start" if args.start is not None else "simulation.start"
        raise ConfigError(
            f"{source} must be an ISO 8601 UTC instant, got {start_value!r}"
        ) from None

    window_hours = simulation.get("window_hours", DEFAULT_WINDOW_HOURS)
    if isinstance(window_hours, bool) or not isinstance(window_hours, (int, float)):
        raise ConfigError(
            f"simulation.window_hours must be a positive number, got {window_hours!r}"
            f" ({type(window_hours).__name__})"
        )
    window_seconds = int(window_hours * 3600)
    if window_seconds < 1:
        raise ConfigError(
            f"simulation.window_hours must cover at least one second, got {window_hours!r}"
        )

    return Settings(sessions=args.sessions, start=start, window_seconds=window_seconds, rates=rates)


def load_ids(config, filename, key):
    path = output_path(config, filename)
    if not path.exists():
        raise ConfigError(f"{path} is missing -- run `make reference` first")
    ids = [record[key] for record in read_jsonl(path)]
    if not ids:
        raise ConfigError(f"{path} is empty")
    return ids


def generate_events(config, settings, user_ids, product_ids):
    """Generate every session's events and return them in chronological order.

    Session `i` draws from its own `"<seed>:session:<i>"` RNG rather than from
    one RNG advanced across the run, so raising --sessions leaves the earlier
    sessions bit-identical and a growing test set stays debuggable.
    """
    arrivals = rng_for(config, "arrivals")
    start_times = [
        settings.start + timedelta(seconds=arrivals.randrange(settings.window_seconds))
        for _ in range(settings.sessions)
    ]

    events = []
    for index, start_time in enumerate(start_times):
        events.extend(
            build_session(
                rng_for(config, f"session:{index}"),
                start_time,
                user_ids,
                product_ids,
                settings.rates,
            )
        )

    # A real stream arrives in time order, with sessions interleaved. Ordering the
    # baseline is what makes a deliberate out-of-order injection measurable later.
    events.sort(key=lambda event: event["event_time"])
    return events


def build_manifest(config, settings, events):
    """The settings this run actually used, plus what it produced.

    Everything here is derived from the config and the arguments: no wall clock and
    no environment, so the same seed and arguments give a byte-identical manifest.
    The loader stores it beside config.yaml in `meta.run_config` (spec 0002).
    """
    return {
        "seed": config["seed"],
        "sessions": settings.sessions,
        "start": format_instant(settings.start),
        "window_hours": settings.window_seconds / 3600,
        "funnel": dict(settings.rates),
        "sessions_written": len({event["session_id"] for event in events}),
        "events_written": len(events),
    }


def print_summary(events, settings, stream):
    """A convenience only: nothing here is needed to interpret the data itself."""
    counts = Counter(event["event_type"] for event in events)
    print(f"sessions {settings.sessions}   events {len(events)}", file=stream)
    for event_type in EVENT_TYPES:
        print(f"  {event_type:<13} {counts[event_type]:>7}", file=stream)
    print(f"  first {events[0]['event_time']}", file=stream)
    print(f"  last  {events[-1]['event_time']}", file=stream)


def main(argv=None):
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        settings = resolve_settings(config, args)
        user_ids = load_ids(config, "users.jsonl", "user_id")
        product_ids = load_ids(config, "products.jsonl", "product_id")
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    events = generate_events(config, settings, user_ids, product_ids)

    # Written before the events themselves: an unwritable manifest path then fails
    # while the redirected output file is still empty, rather than halfway through it.
    if args.manifest is not None:
        try:
            write_json(args.manifest, build_manifest(config, settings, events))
        except OSError as error:
            print(f"error: cannot write the manifest to {args.manifest}: {error}", file=sys.stderr)
            return 2

    write_jsonl_stream(sys.stdout, events)
    print_summary(events, settings, sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
