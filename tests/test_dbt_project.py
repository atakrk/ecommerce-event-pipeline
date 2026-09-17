"""Tests for the dbt project skeleton in transform/: profile path, time zone, schema naming.

uv run pytest tests/test_dbt_project.py

Each test runs dbt against a copy of the committed project files in a temporary
folder, so it never opens data/warehouse.duckdb and needs no `dbt deps` (the
macro and profile do not use dbt_utils). About three seconds per dbt call.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from common import PROJECT_ROOT

TRANSFORM = PROJECT_ROOT / "transform"
DBT = Path(sys.executable).parent / "dbt"

# The warehouse path, session time zone, and generate_schema_name override, read back in one query.
PROBE_SQL = (
    "select '{{ generate_schema_name(\"staging\", none) }}' as custom_schema,"
    " '{{ generate_schema_name(none, none) }}' as default_schema,"
    " '{{ generate_schema_name(\"  marts \", none) }}' as padded_schema,"
    " current_setting('TimeZone') as time_zone"
)


@pytest.fixture
def project(tmp_path):
    """A copy of the committed dbt project files, outside the repo."""
    copy = tmp_path / "transform"
    copy.mkdir()
    for name in ("dbt_project.yml", "profiles.yml"):
        shutil.copy(TRANSFORM / name, copy / name)
    shutil.copytree(TRANSFORM / "macros", copy / "macros")
    return copy


def run_probe(project, cwd, warehouse_path=None):
    env = {key: value for key, value in os.environ.items() if key != "DBT_DUCKDB_PATH"}
    env.update(DO_NOT_TRACK="1", TZ="Europe/Istanbul")
    if warehouse_path is not None:
        env["DBT_DUCKDB_PATH"] = str(warehouse_path)
    result = subprocess.run(
        [
            str(DBT),
            "show",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
            "--target-path",
            str(cwd / "target"),
            "--log-path",
            str(cwd / "logs"),
            "--quiet",
            "--output",
            "json",
            "--inline",
            PROBE_SQL,
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["show"][0]


# covers: DW-4 (dbt runs against the warehouse named by DBT_DUCKDB_PATH, no second file)
def test_profile_opens_the_warehouse_at_dbt_duckdb_path(project, tmp_path):
    warehouse = tmp_path / "elsewhere" / "warehouse.duckdb"
    warehouse.parent.mkdir()
    workdir = tmp_path / "workdir"
    (workdir / "data").mkdir(parents=True)

    run_probe(project, cwd=workdir, warehouse_path=warehouse)

    assert warehouse.exists()
    assert not (workdir / "data" / "warehouse.duckdb").exists()


# covers: DW-4 (fallback when DBT_DUCKDB_PATH is unset)
def test_profile_falls_back_to_data_warehouse_duckdb_in_the_working_directory(project, tmp_path):
    workdir = tmp_path / "workdir"
    (workdir / "data").mkdir(parents=True)

    run_probe(project, cwd=workdir)

    assert (workdir / "data" / "warehouse.duckdb").exists()


# One dbt call checks all three settings, so the suite stays fast.
def test_session_time_zone_is_utc_and_schemas_keep_their_custom_names(project, tmp_path):
    warehouse = tmp_path / "warehouse.duckdb"

    row = run_probe(project, cwd=tmp_path, warehouse_path=warehouse)

    # The host time zone above is set to Istanbul on purpose: the profile must override it.
    assert row["time_zone"] == "UTC"
    assert row["custom_schema"] == "staging"
    assert row["padded_schema"] == "marts"
    assert row["default_schema"] == "main"
