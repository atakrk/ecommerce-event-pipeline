"""Generate user reference data -> data/users.jsonl"""

from datetime import UTC, datetime, timedelta

from common import (
    base_arg_parser,
    ensure_writable,
    load_config,
    output_path,
    print_distribution,
    rng_for,
    weighted_choice,
    write_jsonl,
)

OUTPUT_FILE = "users.jsonl"


def _parse_date(value):
    # YAML turns unquoted dates into date objects; str() handles both cases.
    return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)


def generate_users(config):
    users_cfg = config["users"]
    rng = rng_for(config, "users")

    countries = users_cfg["countries"]
    country_weights = {code: c["weight"] for code, c in countries.items()}

    start = _parse_date(users_cfg["created_at_range"]["start"])
    # The end date is inclusive, so the range runs to the start of the next day.
    end = _parse_date(users_cfg["created_at_range"]["end"]) + timedelta(days=1)
    last_second = int((end - start).total_seconds()) - 1

    for i in range(1, users_cfg["count"] + 1):
        country = weighted_choice(rng, country_weights)
        city = rng.choice(countries[country]["cities"])
        device_type = weighted_choice(rng, users_cfg["device_types"])
        created_at = start + timedelta(seconds=rng.randint(0, last_second))
        yield {
            "user_id": f"U{i:06d}",
            "country": country,
            "city": city,
            "device_type": device_type,
            "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def main():
    args = base_arg_parser(__doc__, force=True).parse_args()
    config = load_config(args.config)
    path = output_path(config, OUTPUT_FILE)
    if not ensure_writable(path, args.force):
        return

    users = list(generate_users(config))
    write_jsonl(path, users)

    print(f"{len(users)} users -> {path}")
    print_distribution("country", (u["country"] for u in users))
    print_distribution("device_type", (u["device_type"] for u in users))


if __name__ == "__main__":
    main()
