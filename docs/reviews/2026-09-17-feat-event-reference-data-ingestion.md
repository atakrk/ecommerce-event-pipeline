# Review, feat/event-reference-data-ingestion, 2026-09-17

**Reviewed by**: Claude Sonnet 5 (author on Claude Opus 5)
**Scope**: 26 files, branch vs main
**Verdict**: Blocked

## Summary
This branch builds the loader (`pipeline/load.py`), the manifest option on the generator, and the first real dbt layer (`stg_raw__*`, `marts.fct_events`) that spec 0002 calls for. The run lifecycle (`running` to `succeeded`/`failed`), the atomic single transaction, the `ASOF LEFT JOIN` for as-of reference resolution, and the dedup-in-the-mart design are all implemented as the spec describes, and `make check` (lint, pytest, load, dbt build) is green on this branch with `data/warehouse.duckdb` left untouched. Test coverage is thorough for the documented scenarios. However, hands-on testing during this review found a genuine data loss bug in the dedup logic: `content_sha256` identity is checked globally across all loaded files rather than per file kind, so two different input files that happen to share the same bytes cause the second one to be silently skipped, with no error and a status line that reads as normal. This is reproducible with trivial fixtures and needs a fix before merge.

## Blockers

### 🔴 Cross-kind content hash collision silently drops a whole file, `pipeline/load.py:207-211`
**Problem**: `already_loaded(connection, digest)` queries `meta.load_files` for `content_sha256 = ?` with no `file_kind` filter:
```python
def already_loaded(connection, digest):
    found = connection.execute(
        "select 1 from meta.load_files where content_sha256 = ? limit 1", [digest]
    ).fetchone()
    return found is not None
```
`load_file` (and `load_manifest`) use this to decide `skipped_duplicate` vs `loaded`. Because the check is global rather than scoped to `file_kind`, two logically different input files that happen to have identical bytes collide. I reproduced this directly: with `users.jsonl` and `products.jsonl` both containing the single line `{"id": 1}`, a single `pipeline.load` run loads `users` (1 row) and then reports `products` as `skipped, already loaded` and writes **zero** rows to `raw.products`, with no warning or error:
```
  users     .../users.jsonl  1 lines
  products  .../products.jsonl  skipped, already loaded
  events    .../events.jsonl  1 lines
total 2 rows   load_id 1
```
`meta.load_files` shows `('products', 'skipped_duplicate', 0)` even though `raw.products` was never loaded before in this warehouse.
**Why it matters**: This is silent data loss on a core acceptance criterion (AC-1: one row per non-empty line into each of `raw.events`, `raw.users`, `raw.products`). It is easy to hit with small or empty fixtures (a common shape for `make check`-style seeded runs, tests, or an early bootstrap where a reference file happens to be empty or trivially small), and there is nothing in the loader's output that distinguishes a real duplicate from a false one — both print `skipped, already loaded`. A user trusting the loader's summary would have no way to know a file was actually dropped.
**Suggested fix**: Scope the duplicate check by `file_kind` as well as `content_sha256` (e.g. `where file_kind = ? and content_sha256 = ?`), so identity is "these exact bytes, for this input", not "these exact bytes, for anything ever loaded." Add a regression test with two same-content, different-kind fixtures like the repro above.

## Minor

### 🟡 A blank line in a CRLF file is stored as an empty `raw_line`, `pipeline/load.py:86-96`
**Problem**: `INSERT_LINES` filters on `where line <> ''` before `rtrim(line, chr(13))` is applied. A blank line in a CRLF file splits (on `chr(10)`) to the single-character string `"\r"`, which passes the `<> ''` filter, then gets stored as `rtrim('\r', chr(13))` = an empty string. I reproduced this: loading `'{"a":1}\r\n\r\n{"a":2}\r\n'` produces raw rows `(1, '{"a":1}')`, `(2, '')`, `(3, '{"a":2}')` — an empty `raw_line` lands in `raw.events`.
**Why it matters**: AC-1 promises "one row per non-empty line", and the design's own blank-line rule (`rationale.md`, "Blank and trailing lines") says blank lines should be dropped before insertion regardless of line ending. Here an LF-only blank line is correctly dropped (per `tests/test_load.py::test_line_numbers_follow_the_file_even_across_blank_lines`) but the CRLF case is not, which is an inconsistency between the two documented rules. Practically low-impact today (the pipeline's own writers use LF, and `json_valid` filters the resulting empty string out of staging), but it is a real gap for any externally supplied CRLF file, and it silently pollutes `raw.*` with an empty-string row.
**Suggested fix**: Filter on the trimmed value, e.g. `where rtrim(line, chr(13)) <> ''`, so both LF and CRLF blank lines are dropped consistently before insertion.

### 🟡 Whitespace-only lines are kept, contradicting the documented rule, `pipeline/load.py:86-96`
**Problem**: `rationale.md` ("Smaller calls made while writing") states the design drops "the empty and whitespace only" lines, but the actual filter is only `where line <> ''`. A line containing only spaces is inserted as-is. I confirmed this: a file with `'{"a":1}\n   \n{"a":2}\n'` stores line 2 as raw_line `'   '` rather than dropping it.
**Why it matters**: Low practical impact (`json_valid` drops it in staging, so it never reaches `fct_events`), but it is a documented invariant the code does not implement, and it adds noise to the raw-to-staging coverage warn test for no reason.
**Suggested fix**: Either update the filter to trim before comparing (`where trim(line) <> ''`), or update `rationale.md` to say only strictly-empty lines are dropped, so the code and the documented design agree.

### 🟡 Unhandled exception path between `nextval` and `begin_run`, `pipeline/load.py:332-337`
**Problem**: `create_objects(connection, ...)`, the `nextval` call, and `begin_run(connection, load_id)` all run inside the outer `try` block, but that block has no `except` clause of its own (only the inner `try` around `load_all` catches `Exception`). If `create_objects` or `begin_run` themselves raise (e.g. a DDL or constraint error), the exception propagates as a raw Python traceback instead of the `error: ...`-prefixed stderr message the rest of the module uses consistently.
**Why it matters**: Minor in likelihood (these are simple, well-tested DDL/DML statements), but it is an inconsistency in the module's own error-handling convention, and a raw traceback on stderr is a worse experience than the guided messages used everywhere else in this file.
**Suggested fix**: Either wrap this section the same way `load_all` is wrapped (rollback is a no-op here since no transaction is open yet) or note explicitly why this segment is exempt.

## Nits
- ⚪ `common.py:100-132`, `write_jsonl` and `write_json` duplicate the same "tmp file, write, `os.replace`, unlink on failure" scaffolding almost verbatim. Worth a small shared helper if a third writer shows up.

## Strengths
- The run lifecycle exactly matches the spec's carefully reasoned ordering: `running` committed alone right after `nextval`, `succeeded` committed inside the same transaction as the data, `failed` committed only after rollback. `tests/test_load.py::test_a_failed_run_is_recorded_and_leaves_no_data` and the manually verified crash-window scenario in `verify.md` both back this up, and it held up under a real `SIGKILL` test per the verify log.
- The dbt layer is clean and does exactly what spec 0002 asks: `json_valid` before `json_extract_string` (avoiding the `InvalidInputException` trap), `try_cast` distinguishing a present-but-uncastable value from a JSON `null`, and `asof left join` (not a hand-rolled window function) for as-of reference resolution, including the `LEFT` form that keeps orphan events per AC-9.
- `make check` genuinely isolates its seeded run: I ran it end to end and confirmed `data/warehouse.duckdb`'s checksum is unchanged before and after, matching AC-11.
- Good, specific test names throughout `tests/test_load.py` and `tests/test_run.py` that describe the behavior under test rather than the function under test, and the idempotency, `--force`, missing-input, missing-manifest, and locked-warehouse scenarios are all exercised end to end against a real temporary DuckDB file rather than mocked.

## Test coverage
The documented scenarios (idempotency, `--force`, missing input, missing manifest, locked warehouse, failed run rollback, stdout/stderr discipline, manifest determinism) all have direct pytest coverage, and the dbt side is covered by data tests plus manually-run `verify.md` steps for the as-of join, orphan references, and deduplication survivor rule. The gap is the cross-kind hash collision above: nothing in `tests/test_load.py` loads two different-kind files with identical content, so the false-duplicate bug shipped undetected. There is also no automated (pytest or dbt unit test) coverage of the as-of join's "keeps the old value for the earlier load" behavior — it is currently verified only by the manual step in `verify.md`, which is reasonable for this stage but worth turning into a dbt unit test before `/test` locks this feature in.
