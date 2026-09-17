"""Tests that keep the toolchain pins from spec 0001 consistent with each other.

uv run pytest tests/test_project_setup.py
"""

import sys
import tomllib
from importlib.metadata import version

import yaml
from packaging.specifiers import SpecifierSet

from common import PROJECT_ROOT


def read_toml(name):
    return tomllib.loads((PROJECT_ROOT / name).read_text(encoding="utf-8"))


def pinned(requirement_prefix, requirements):
    """The specifier of the one requirement that names this package."""
    (match,) = [r for r in requirements if r.split("~=")[0].split(">=")[0] == requirement_prefix]
    return SpecifierSet(match[len(requirement_prefix) :])


# covers: DW-1
def test_runs_on_python_3_12():
    assert sys.version_info[:2] == (3, 12)
    assert (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


# covers: DW-1 (duckdb, dbt-core, dbt-duckdb stay on one minor version)
def test_installed_warehouse_tools_match_their_minor_version_pins():
    requirements = read_toml("pyproject.toml")["project"]["dependencies"]
    for package in ("duckdb", "dbt-core", "dbt-duckdb"):
        specifier = pinned(package, requirements)
        assert "~=" in str(specifier), f"{package} must be pinned with ~="
        assert version(package) in specifier, f"{package} {version(package)} not in {specifier}"


def test_pre_commit_ruff_matches_the_locked_ruff_version():
    config = yaml.safe_load((PROJECT_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    (ruff_repo,) = [repo for repo in config["repos"] if repo["repo"].endswith("ruff-pre-commit")]
    locked = {package["name"]: package["version"] for package in read_toml("uv.lock")["package"]}
    assert ruff_repo["rev"] == f"v{locked['ruff']}"


def test_dbt_utils_lock_satisfies_packages_yml():
    transform = PROJECT_ROOT / "transform"
    wanted = yaml.safe_load((transform / "packages.yml").read_text(encoding="utf-8"))["packages"]
    locked = yaml.safe_load((transform / "package-lock.yml").read_text(encoding="utf-8"))[
        "packages"
    ]
    (utils,) = [p for p in wanted if p["package"] == "dbt-labs/dbt_utils"]
    (utils_lock,) = [p for p in locked if p["package"] == "dbt-labs/dbt_utils"]
    assert utils_lock["version"] in SpecifierSet(",".join(utils["version"]))
