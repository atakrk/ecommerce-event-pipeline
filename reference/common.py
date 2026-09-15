"""Referans veri üreticilerinin ortak yardımcıları."""
import argparse
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def base_arg_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="config.yaml yolu")
    parser.add_argument("--force", action="store_true", help="Var olan dosyanın üzerine yaz")
    return parser


def load_config(path=DEFAULT_CONFIG):
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def rng_for(config, name):
    """Her üretici için ayrı, deterministik RNG.

    Seed `"<seed>:<name>"` olduğu için users ve products birbirinden bağımsızdır:
    birinin config'ini değiştirmek diğerinin çıktısını kaydırmaz.
    """
    return random.Random(f"{config['seed']}:{name}")


def weighted_choice(rng, weights_by_key):
    keys = list(weights_by_key)
    return rng.choices(keys, weights=[weights_by_key[k] for k in keys])[0]


def output_path(config, filename):
    return PROJECT_ROOT / config["paths"]["data_dir"] / filename


def ensure_writable(path, force):
    """Referans veri bir kez üretilir: dosya varsa --force olmadan dokunma."""
    if path.exists() and not force:
        print(f"{path} zaten var, atlanıyor. Yeniden üretmek için --force kullan.", file=sys.stderr)
        return False
    return True


def write_jsonl(path, records):
    """Önce .tmp'ye yazar, sonra atomik olarak taşır — yarım dosya kalmaz."""
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
