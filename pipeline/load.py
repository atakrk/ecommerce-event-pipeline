"""Load the generated JSONL files into DuckDB -> raw.* tables, plus a meta run record.

    python -m pipeline.load
    python -m pipeline.load --database /tmp/warehouse.duckdb --force
    python -m pipeline.load path/to/events.jsonl        # override the config defaults

Every line lands in `raw.<kind>` untouched, with its real file line number: parsing,
typing, deduplicating and joining are dbt's job (spec 0002). A file whose contents
were already loaded is skipped, so rerunning the command is always safe.

Diagnostics go to stderr; this command writes nothing to stdout.
"""

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from common import PROJECT_ROOT, ConfigError, base_arg_parser, load_config, output_path

DEFAULT_WAREHOUSE = "data/warehouse.duckdb"
MANIFEST_FILE = "events.manifest.json"

# The three files a plain run expects, in load order: reference data is small and
# loading it first means an interrupted run still leaves the dimensions in place.
INPUT_FILES = (("users", "users.jsonl"), ("products", "products.jsonl"), ("events", "events.jsonl"))

# A file kind becomes a table name, so it may only ever be a plain lowercase identifier.
KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

LOCKED_WAREHOUSE_HINT = (
    "{path} is open in another process. Close the DuckDB MCP server or the DuckDB UI "
    "and run this again ({detail})"
)

# `raw` rows reference meta.load_runs(load_id), but DuckDB cannot declare a foreign key
# across schemas, so that link is kept by this module rather than by the engine.
RAW_TABLE_DDL = """
create table if not exists raw.{kind} (
    load_id bigint not null,
    source_file varchar not null,
    line_number bigint not null,
    raw_line varchar not null,
    primary key (load_id, line_number)
)
"""

META_DDL = (
    "create schema if not exists raw",
    "create schema if not exists meta",
    "create sequence if not exists meta.load_id_seq start 1",
    """create table if not exists meta.load_runs (
        load_id bigint primary key,
        started_at timestamp not null,
        ended_at timestamp,
        status varchar not null,
        error_message varchar,
        total_rows bigint
    )""",
    """create table if not exists meta.load_files (
        load_id bigint not null,
        file_kind varchar not null,
        source_file varchar not null,
        content_sha256 varchar not null,
        line_count bigint not null,
        status varchar not null,
        primary key (load_id, file_kind)
    )""",
    """create table if not exists meta.run_config (
        load_id bigint not null,
        source varchar not null,
        key varchar not null,
        value varchar,
        primary key (load_id, source, key)
    )""",
)

# One statement per file: the text never passes through Python, only its hash does.
# `generate_subscripts` numbers the split before empty lines are dropped, so a line
# number always points at the real line in the file.
INSERT_LINES = """
insert into raw.{kind} (load_id, source_file, line_number, raw_line)
select ?, ?, line_number, rtrim(line, chr(13))
from (
    select
        unnest(str_split(content, chr(10))) as line,
        generate_subscripts(str_split(content, chr(10)), 1) as line_number
    from read_text(?)
)
where line <> ''
"""


def parse_args(argv=None):
    parser = base_arg_parser(__doc__)
    parser.add_argument("--database", type=Path, help="Warehouse file to load into")
    parser.add_argument(
        "--manifest", type=Path, help=f"Generator manifest to record (default {MANIFEST_FILE})"
    )
    parser.add_argument(
        "--force", action="store_true", help="Load a file even if its contents were loaded before"
    )
    parser.add_argument(
        "paths", nargs="*", type=Path, help="Input files, overriding the config defaults"
    )
    return parser.parse_args(argv)


def utc_now():
    """Load metadata is not generator output, so it uses the wall clock, not simulated time."""
    return datetime.now(UTC).replace(tzinfo=None)


def under_project_root(value):
    """Resolve a path; a relative one hangs off the repo root, not the working directory."""
    return (PROJECT_ROOT / Path(value).expanduser()).resolve()


def resolve_database(database, config, env):
    """Precedence: --database, then DBT_DUCKDB_PATH, then config, then the default."""
    paths = config.get("paths") or {}
    for candidate in (database, env.get("DBT_DUCKDB_PATH"), paths.get("warehouse")):
        if candidate:
            return under_project_root(candidate)
    return under_project_root(DEFAULT_WAREHOUSE)


def resolve_inputs(config, paths):
    """The three expected files, or the positional overrides, each paired with its kind."""
    if paths:
        return tuple((Path(path).stem, under_project_root(path)) for path in paths)
    return tuple((kind, output_path(config, name).resolve()) for kind, name in INPUT_FILES)


def resolve_manifest(config, manifest):
    if manifest:
        return under_project_root(manifest)
    return output_path(config, MANIFEST_FILE).resolve()


def readable(path):
    return path.is_file() and os.access(path, os.R_OK)


def check_inputs(inputs):
    """Reject a bad kind or an unreadable file before anything is written or numbered."""
    for kind, path in inputs:
        if not KIND_PATTERN.match(kind):
            raise ConfigError(f"{path} gives the file kind {kind!r}, which is not a table name")
        if not readable(path):
            raise ConfigError(f"{path} is missing or unreadable")


def sha256_of(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def flat_value(value):
    """One config leaf as text. A list or an empty mapping keeps its JSON shape."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def flatten(mapping, prefix=""):
    """Flatten nested settings into ordered (dotted key, text value) pairs."""
    rows = []
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            rows.extend(flatten(value, f"{dotted}."))
        else:
            rows.append((dotted, flat_value(value)))
    return tuple(rows)


def connect(database):
    """Open the warehouse, turning DuckDB's single-writer lock into a usable message."""
    database.parent.mkdir(parents=True, exist_ok=True)
    try:
        return duckdb.connect(str(database))
    except duckdb.IOException as error:
        # The exception class is the interface here; the message text is not stable.
        raise ConfigError(LOCKED_WAREHOUSE_HINT.format(path=database, detail=error)) from None


def create_objects(connection, kinds):
    for statement in META_DDL:
        connection.execute(statement)
    for kind in kinds:
        connection.execute(RAW_TABLE_DDL.format(kind=kind))


def already_loaded(connection, digest):
    found = connection.execute(
        "select 1 from meta.load_files where content_sha256 = ? limit 1", [digest]
    ).fetchone()
    return found is not None


def record_file(connection, load_id, kind, path, digest, line_count, status):
    connection.execute(
        "insert into meta.load_files values (?, ?, ?, ?, ?, ?)",
        [load_id, kind, str(path), digest, line_count, status],
    )


def record_config(connection, load_id, source, rows):
    connection.executemany(
        "insert into meta.run_config values (?, ?, ?, ?)",
        [[load_id, source, key, value] for key, value in rows],
    )


def insert_lines(connection, load_id, kind, path):
    inserted = connection.execute(
        INSERT_LINES.format(kind=kind), [load_id, str(path), str(path)]
    ).fetchone()
    return inserted[0]


def load_file(connection, load_id, kind, path, force):
    """Copy one file into its raw table, unless those exact bytes were loaded before."""
    digest = sha256_of(path)
    if already_loaded(connection, digest) and not force:
        record_file(connection, load_id, kind, path, digest, 0, "skipped_duplicate")
        return kind, path, "skipped_duplicate", 0
    line_count = insert_lines(connection, load_id, kind, path)
    record_file(connection, load_id, kind, path, digest, line_count, "loaded")
    return kind, path, "loaded", line_count


def load_manifest(connection, load_id, path, force):
    """Record the generator's settings. An absent manifest is a warning, never an error."""
    if not readable(path):
        warning = f"warning: no manifest at {path}, loading without generator settings"
        print(warning, file=sys.stderr)
        return None
    digest = sha256_of(path)
    if already_loaded(connection, digest) and not force:
        # line_count stays 0 for the manifest: it contributes settings, not raw rows.
        record_file(connection, load_id, "manifest", path, digest, 0, "skipped_duplicate")
        return "manifest", path, "skipped_duplicate", 0
    settings = json.loads(path.read_text(encoding="utf-8"))
    record_config(connection, load_id, "manifest", flatten(settings))
    record_file(connection, load_id, "manifest", path, digest, 0, "loaded")
    return "manifest", path, "loaded", 0


def begin_run(connection, load_id):
    """Commit the run row on its own, before any data exists.

    A row left at `running` therefore always means no data from that run was committed:
    the only commit that writes data also moves the status to `succeeded`.
    """
    connection.execute(
        "insert into meta.load_runs (load_id, started_at, status) values (?, ?, 'running')",
        [load_id, utc_now()],
    )


def finish_run(connection, load_id, total_rows):
    connection.execute(
        "update meta.load_runs set status = 'succeeded', ended_at = ?, total_rows = ? "
        "where load_id = ?",
        [utc_now(), total_rows, load_id],
    )


def fail_run(connection, load_id, message):
    """Written only after the data transaction has rolled back, so `failed` means no data."""
    connection.execute(
        "update meta.load_runs set status = 'failed', ended_at = ?, error_message = ? "
        "where load_id = ?",
        [utc_now(), message, load_id],
    )


def load_all(connection, load_id, inputs, manifest, config, force):
    """Every write of one run, in the single transaction that also records its success."""
    connection.execute("begin transaction")
    results = [load_file(connection, load_id, kind, path, force) for kind, path in inputs]
    record_config(connection, load_id, "config", flatten(config))
    manifest_result = load_manifest(connection, load_id, manifest, force)
    if manifest_result is not None:
        results.append(manifest_result)
    total_rows = sum(line_count for *_, line_count in results)
    finish_run(connection, load_id, total_rows)
    connection.execute("commit")
    return tuple(results), total_rows


def describe(kind, status, line_count):
    if status == "skipped_duplicate":
        return "skipped, already loaded"
    # The manifest contributes settings rather than raw rows, so it has no line count.
    return "settings recorded" if kind == "manifest" else f"{line_count} lines"


def print_summary(results, total_rows, load_id, stream):
    for kind, path, status, line_count in results:
        print(f"  {kind:<9} {path}  {describe(kind, status, line_count)}", file=stream)
    print(f"total {total_rows} rows   load_id {load_id}", file=stream)


def main(argv=None):
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        database = resolve_database(args.database, config, os.environ)
        inputs = resolve_inputs(config, args.paths)
        manifest = resolve_manifest(config, args.manifest)
        check_inputs(inputs)
        connection = connect(database)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    create_objects(connection, [kind for kind, _ in inputs])
    # Last, because a DuckDB sequence value is never rolled back: taking it before the
    # checks above would burn a load_id with no record that it ever existed.
    load_id = connection.execute("select nextval('meta.load_id_seq')").fetchone()[0]
    begin_run(connection, load_id)

    try:
        results, total_rows = load_all(connection, load_id, inputs, manifest, config, args.force)
    except Exception as error:
        connection.execute("rollback")
        fail_run(connection, load_id, str(error))
        print(f"error: load {load_id} failed and was rolled back: {error}", file=sys.stderr)
        return 1

    print_summary(results, total_rows, load_id, sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
