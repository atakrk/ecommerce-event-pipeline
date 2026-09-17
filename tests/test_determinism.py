"""End to end determinism: the generators must keep producing byte identical output.

    uv run pytest tests/test_determinism.py

The golden hashes were recorded from the Python 3.9 code before the move to
Python 3.12 (scope feature 0). A changed hash means a seed now produces different
data, which breaks every comparison a later pipeline run makes against earlier ones.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from common import PROJECT_ROOT

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "config.yaml"
SESSIONS = "300"

GOLDEN_SHA256 = {
    "users.jsonl": "6ffe2ad2d78952daae4ff2ac769c6f5bd83b2b054e5471c534083f1ce7f04373",
    "products.jsonl": "1a80f4a19d4619c9be76ddf6292162d0b7a133af82bd899136ba314d0154d4d1",
    "events.jsonl": "38590aef79eef93d311f6c33acd8bc84cdc9588fc6591289f5d094a42fd58957",
    # The manifest is pinned for the same reason: it is the record of what produced
    # the events, so a run that is reproducible must describe itself identically too.
    "events.manifest.json": "ba27f7c81e4fdb33be7bf93277fce044ec34e8f9c75bba65ca4e47c5aacc6356",
}


def run(*args, stdout=None):
    return subprocess.run(
        [sys.executable, *args],
        cwd=PROJECT_ROOT,
        stdout=stdout if stdout is not None else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )


def generate(data_dir):
    """Write reference data and events for the fixture config into `data_dir`."""
    config = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config["paths"]["data_dir"] = str(data_dir)
    config_path = data_dir / "config.yaml"
    # sort_keys=False: weighted choices follow the key order in the config.
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    run("-m", "reference.generate_users", "--config", str(config_path))
    run("-m", "reference.generate_products", "--config", str(config_path))
    with (data_dir / "events.jsonl").open("wb") as events:
        run(
            "-m",
            "generator.run",
            "--config",
            str(config_path),
            "--sessions",
            SESSIONS,
            "--manifest",
            str(data_dir / "events.manifest.json"),
            stdout=events,
        )
    return config_path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    return data_dir, generate(data_dir)


@pytest.mark.parametrize("filename", sorted(GOLDEN_SHA256))
def test_output_matches_golden_hash(generated, filename):
    data_dir, _ = generated
    assert sha256(data_dir / filename) == GOLDEN_SHA256[filename]


def test_verify_passes_on_generated_events(generated):
    data_dir, config_path = generated
    result = run("verify.py", "--config", str(config_path), stdout=subprocess.PIPE)
    assert b"ordered by event_time: yes" in result.stdout
