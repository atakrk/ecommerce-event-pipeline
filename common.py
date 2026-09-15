"""Shared helpers for the reference data and event generators."""
import argparse
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def base_arg_parser(description, force=False):
    """Argument parser with the options every generator shares.

    `force` is opt-in: only generators that write a file in place can overwrite one.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.yaml")
    if force:
        parser.add_argument("--force", action="store_true", help="Overwrite the existing output file")
    return parser


def load_config(path=DEFAULT_CONFIG):
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def rng_for(config, name):
    """Independent, deterministic RNG for each generator.

    Seeding with `"<seed>:<name>"` keeps users and products independent:
    changing one generator's config does not shift the other's output.
    """
    return random.Random(f"{config['seed']}:{name}")


def weighted_choice(rng, weights_by_key):
    keys = list(weights_by_key)
    return rng.choices(keys, weights=[weights_by_key[k] for k in keys])[0]


def output_path(config, filename):
    return PROJECT_ROOT / config["paths"]["data_dir"] / filename


def ensure_writable(path, force):
    """Reference data is generated once: leave an existing file alone unless --force is given."""
    if path.exists() and not force:
        print(f"{path} already exists, skipping. Use --force to regenerate.", file=sys.stderr)
        return False
    return True


def write_jsonl(path, records):
    """Write to a .tmp file first, then move it into place atomically so no partial file is left behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def print_distribution(title, values):
    counts = Counter(values)
    total = sum(counts.values())
    print(f"  {title}:")
    for key, count in counts.most_common():
        print(f"    {key:<12} {count:>6}  ({count / total:.1%})")
