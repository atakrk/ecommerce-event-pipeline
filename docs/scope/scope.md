# Scope: Ecommerce Event Pipeline

A pipeline that generates synthetic ecommerce behavior events (page views, product views, add to cart, checkout, purchase) and grows, layer by layer, into a full ingest, validate, and analyze pipeline. A personal project meant to teach real data engineering patterns end to end.

**Build approach:** Skateboard (ship the thinnest usable whole first, then grow it).
**Workflow:** Beta (after /develop: /check verify, then /test). The project default level of rigor. `/architect` is the recommended first stop for a feature with a real decision, but skippable when you already know the build. Any feature can carry its own tag (e.g. `· GA`) to do more or less.

_These are recommendations to keep your build orderly, not requirements. Skip anything that does not fit: if you already know how to build a feature, use `/develop` and skip `/architect`. You decide when a feature is done._

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| A | Reference data generation | Existing | existing |
| B | Event stream generator | Existing | existing |
| C | Funnel structural verification | Existing | existing |
| 0 | Stack and architecture | Release 1 | done |
| 1 | Event and reference data ingestion | Release 1 | done |
| 2 | Schema validation on ingest | Release 1 | planned |
| 3 | Funnel and conversion reporting | Release 1 | planned |
| 4 | Configurable event stream messiness | Release 2 | planned |
| 5 | Data quality metrics across runs | Release 3 | planned |
| 6 | Funnel and volume anomaly detection | Release 4 | planned |

## Existing

### A. Reference data generation · existing
Deterministic generation of the user and product tables every event references.
code in `reference/`

### B. Event stream generator · existing
Reads the reference data and generates funnel shaped sessions of events, written sorted by event_time.
code in `generator/`

### C. Funnel structural verification · existing
Checks generated events against the configured funnel: structural pass or fail rules, plus a funnel percentage report.
code in `verify.py`

## Release 1: Ingest, validate, and report

The thinnest usable whole: events land in a queryable store, bad records get caught on the way in, and the funnel report already known from `verify.py` becomes a real query instead of a one off script.

### 0. Stack and architecture · done
Move the repo onto the stack decided in spec 0001 (Python 3.12, uv, Ruff, pytest, CI, and an empty DuckDB and dbt skeleton) so every Release 1 feature starts from the same foundation.
**Done when:** `uv sync` sets up Python 3.12 with locked dependencies, the existing `make reference`, `make events` and `make verify` still produce byte identical output for the same seed, Ruff and pytest pass locally and in GitHub Actions, and `dbt build` runs cleanly against `data/warehouse.duckdb` from an empty `transform/` project.
spec [0001](../specs/0001-pipeline-stack-architecture/index.md) · from spec 0001 · code in `pyproject.toml`, `transform/`, `tests/`, `.github/workflows/`
- [x] Decide the stack (spec): `/architect stack & architecture`
- [x] Scaffold from the decision: `/develop stack and architecture`
- [x] Verify it: `/check verify stack and architecture`
- [x] Test it: `/test stack and architecture`

### 1. Event and reference data ingestion · done
Load events and reference data into a queryable store so later validation and analytics have something real to query, instead of scanning flat files by hand.
**Done when:** a load command reads `data/events.jsonl`, `data/users.jsonl` and `data/products.jsonl` into a queryable store with each event joined to its user and product; running the load again on the same files does not create duplicate rows.
spec [0002](../specs/0002-event-reference-data-ingestion/index.md) · code in `pipeline/`, `transform/models/`, `transform/tests/`, `tests/test_load.py`
- [x] Design it (spec): `/architect event and reference data ingestion`
- [x] Build it: `/develop event and reference data ingestion`
  - [x] Loader and `make load`: raw and meta tables, all three files, content hash skipping and `--force` (AC-1, AC-2, AC-3, AC-6, AC-7, AC-8)
  - [x] Generation manifest: `--manifest` on the generator, loaded into `meta.run_config` (AC-5)
  - [x] Loader test suite: unit tests plus end to end loads against a temporary warehouse (AC-1 to AC-8)
  - [x] dbt models: the `raw` source, the three staging models, `marts.fct_events`, and their tests (AC-4, AC-9, AC-10)
  - [x] `make pipeline` and CI: load then build, with `make check` using a temporary warehouse (AC-11)
- [x] Verify it: `/check verify event and reference data ingestion`
- [x] Test it: `/test event and reference data ingestion`

### 2. Schema validation on ingest · needs a decision
Reject or quarantine malformed records at load time, the way a real pipeline would, instead of trusting the generator's output blindly.
**Done when:** a record missing a required field, carrying a wrong type, or naming an unknown event_type is written to a quarantine location instead of the main store, and the load run reports how many records were quarantined and why.
- [ ] Design it (spec): `/architect schema validation on ingest`

### 3. Funnel and conversion reporting · needs a decision
Turn the funnel report `verify.py` already produces into a repeatable query over the loaded store, with cuts by segment.
**Done when:** a report command queries the store for stage by stage conversion, matches `verify.py`'s numbers on the same input, and can cut the funnel by country, device_type, and category.
- [ ] Design it (spec): `/architect funnel and conversion reporting`

## Release 2: Realistic messiness

### 4. Configurable event stream messiness · needs a decision
Give the generator the ability to inject realistic mess (duplicate events, out of order arrival, late arriving dimension changes) at a configurable rate, so the ingestion and validation layers from Release 1 have something real to prove themselves against.
**Done when:** config settings control a duplicate rate, an out of order arrival rate, and a late arriving dimension change rate; at rate zero the output is unchanged from today, and injected mess is visible in the output (for example, an event whose arrival order differs from its event_time order).
- [ ] Design it (spec): `/architect configurable event stream messiness`

## Release 3: Quality over time

### 5. Data quality metrics across runs · needs a decision
Track quality signals across runs, not just pass or fail once, so drift becomes visible over time.
**Done when:** each load run records its quality metrics (null rate, duplicate rate, late arrival rate, quarantine rate) keyed by run, and a report command shows how these metrics move across the last N runs.
- [ ] Design it (spec): `/architect data quality metrics across runs`

## Release 4: Anomaly detection

### 6. Funnel and volume anomaly detection · needs a decision
Flag a run whose funnel or volume looks statistically off, beyond the fixed structural checks `verify.py` already does.
**Done when:** a run whose observed funnel or volume moves beyond a configurable threshold from the configured funnel is flagged, naming which stage and by how much; a run within normal sampling noise is not flagged.
- [ ] Design it (spec): `/architect funnel and volume anomaly detection`

## Deferred
Out of scope for the current build pass, kept so the plan stays honest.
- **Session and behavior analysis**: time to convert, session length, drop off points within a session · needs a decision
- **Cohort and retention views**: group users by first activity period and track behavior over time · needs a decision
- **Interactive web dashboard**: charts and filters in place of the query plus report analytics · needs a decision
- **Incremental pipeline runs**: simulate the pipeline running repeatedly over time, for example day over day batches · needs a decision

## Legend

**The decision box.** Every feature carries exactly one, the sub task whose label ends with `(spec)`. Its wording varies, so skills locate it by that `(spec)` suffix, never by an exact label. Every other box is an execution box and `/architect` never ticks one.

**Feature lifecycle**: the scope updates as a feature moves; each row is what it shows and who sets it:

| State | Set by | The feature shows |
|---|---|---|
| `planned` · needs a decision | `/scope` | one box: `Design it (spec): /architect <feature>` |
| `in-progress` (designed) | **`/architect` at spec capture** | `Design it` ticked; spec linked; `Build it: /develop <feature>` + 2 to 5 milestones; the tier's closing boxes (`Verify it`, `Test it`); any surfaced follow up enrolled |
| `in-progress` (building) | `/develop` | milestone sub boxes tick one by one; code pointer filled |
| `in-progress` (verified) | `/check verify` | `Build it` + milestones ticked; `Verify it` ticked |
| `done` | **you, when you decide it is** (any skill sets it when you say so); `/sync` reconciles | boxes you ran ticked, skipped ones marked skipped; for this project's Beta tier, after `/test` is the suggested point to call it done |

- **Next step** = the first unticked box (always a command or a tracked milestone).
- **needs a decision** = run `/architect` first. The tag drops once the spec is captured.
- **Atomic build tasks live in the spec's `## Build plan`, not here**: the scope carries only the milestone rollup.
- **Status** `planned` → `in-progress` → `done`, plus `existing` (pre workflow) and `dropped` (out of scope, kept for history).
- **Workflow** (header line) is the project default, what runs after `/develop`: here, Beta means `/check verify` then `/test`. A feature can carry its own tag (e.g. `· GA`) to do more or less; no tag means it inherits the default.
- **Pointer line** (`spec <n> · code in <path>`): the spec link added by `/architect`, the code path by `/develop`.
