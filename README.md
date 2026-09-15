# ecommerce-event-pipeline

A data pipeline that synthetically generates user behavior events for an e-commerce site (product views, add-to-cart, checkout, purchase) and processes them end to end. The goal is to build event processing, validation, and analytics step by step on top of a realistic but controllable data source.

## Roadmap

- [x] **Step 1 — Repo and reference data:** user and product tables
- [x] **Step 2 — Event generation:** read the reference data and generate funnel events
- [ ] _Next steps — TBD_

## Setup

Requirements: Python 3.9+

```bash
make venv        # creates .venv and installs dependencies
make reference   # generates data/users.jsonl and data/products.jsonl
make events      # generates data/events.jsonl
```

Other commands:

| Command                | What it does                                       |
| ---------------------- | -------------------------------------------------- |
| `make reference`       | Generates reference data; skips files that exist   |
| `make reference-force` | Regenerates, overwriting existing files            |
| `make events`          | Generates `data/events.jsonl` from the reference data |
| `make verify`          | Checks `data/events.jsonl` against the configured funnel |
| `make clean-data`      | Deletes `data/*.jsonl`                             |

The scripts can also be run directly:

```bash
.venv/bin/python -m reference.generate_users [--config config.yaml] [--force]
.venv/bin/python -m reference.generate_products [--config config.yaml] [--force]

.venv/bin/python -m generator.run [--config config.yaml] [--sessions 1000] \
                                  [--start 2026-09-01T00:00:00Z] [--seed N] > data/events.jsonl

.venv/bin/python verify.py [--config config.yaml] [data/events.jsonl]
```

`--seed` overrides the config seed for one-off experiments; the config value stays the default,
so a bare run is reproducible. The event generator writes to **stdout** and its run summary to
**stderr**, so redirecting the stream leaves the data clean.

## Configuration

All parameters live in [`config.yaml`](config.yaml): `seed`, user/product counts, country–city and device distributions, category weights and price ranges, the simulation window, and funnel conversion rates.

Funnel rates are validated before anything is generated: a rate outside `[0, 1]`, a non-numeric
rate, a `--sessions` below 1 or an unparseable `--start` fails with a message naming the offending
key and its value.

## Reference data schemas

### `data/users.jsonl` — 10,000 rows

| Field         | Type   | Example                  | Notes                                  |
| ------------- | ------ | ------------------------ | -------------------------------------- |
| `user_id`     | string | `"U000001"`              | Sequential, unique                     |
| `country`     | string | `"TR"`                   | ISO 3166-1 alpha-2, weighted           |
| `city`        | string | `"Istanbul"`             | Always a city belonging to `country`   |
| `device_type` | string | `"mobile"`               | `mobile` / `desktop` / `tablet`        |
| `created_at`  | string | `"2024-03-12T14:22:05Z"` | ISO 8601 UTC                           |

### `data/products.jsonl` — 1,000 rows

| Field        | Type   | Example         | Notes                                              |
| ------------ | ------ | --------------- | -------------------------------------------------- |
| `product_id` | string | `"P00001"`      | Sequential, unique                                 |
| `category`   | string | `"electronics"` | Weighted                                           |
| `price`      | float  | `149.99`        | Log-uniform within the category range, 2 decimals  |

## Verifying the output

`verify.py` reads an event file and reports the observed funnel next to the configured one:

```
stage                        configured   observed     diff
page_view_to_product_view         60.0%      61.1%     +1.1
product_view_to_add_to_cart       15.0%      15.2%     +0.2
add_to_cart_to_checkout           50.0%      67.7%    +17.7
checkout_to_purchase              60.0%      57.1%     -2.9

sessions 1000   events 1803   ordered by event_time: yes
```

The percentages are a report, not a verdict — they drift with sample size, and the later stages
drift hardest because few sessions reach them. The `+17.7` above is 63 checkouts out of 93 carts;
at 400,000 sessions the same stage lands on 50.6%.

The structural checks *are* pass/fail. Any failure is written to stderr and exits non-zero:

- every `session_id` has exactly one `PAGE_VIEW`, and it is the earliest event of that session
- no session skips a stage — a `CHECKOUT` always has an `ADD_TO_CART` before it
- every non-`PAGE_VIEW` event of a session carries the same `product_id`
- `event_id` values are unique across the file
- the file is sorted by `event_time`

## Event schema

### `data/events.jsonl`

| Field        | Type          | Example                                  | Notes                                            |
| ------------ | ------------- | ---------------------------------------- | ------------------------------------------------ |
| `event_id`   | string        | `"262d1584-e548-40f7-aaa6-17d343d86a86"` | UUID, unique across the file                     |
| `session_id` | string        | `"7624fbe2-19ed-4f10-b55d-04e60f797693"` | UUID, same for every event of one visit          |
| `user_id`    | string        | `"U008914"`                              | Joins to `users.jsonl`                           |
| `event_type` | string        | `"PRODUCT_VIEW"`                         | `PAGE_VIEW` / `PRODUCT_VIEW` / `ADD_TO_CART` / `CHECKOUT` / `PURCHASE` |
| `event_time` | string        | `"2026-09-01T00:06:06Z"`                 | ISO 8601 UTC                                     |
| `product_id` | string / null | `"P00521"`                               | `null` on `PAGE_VIEW`, joins to `products.jsonl` |

## Design decisions

- **Reference data is generated once.** In a real system, users and products exist before any events. The event generator only reads these files; the scripts never overwrite an existing file without `--force`.
- **Small scale (10k users / 1k products).** Enough to learn the pipeline logic; bigger files only slow down iteration.
- **Deterministic generation.** Same `seed` → byte-identical files. The user and product generators use separately derived seeds, so changing one does not affect the other. That's why `data/` is not committed.
- **Atomic writes.** Reference files are written to a `.tmp` file first and then moved into place, so an interrupted run never leaves a corrupt file behind.
- **Events carry no dimensions.** `country`, `city`, `device_type` and `price` live in the reference files, not on the event. The pipeline joins them. An event stream that carries its own dimensions cannot teach a join, and hides the late-arriving-dimension problem entirely.
- **One product per session.** The product chosen at `PRODUCT_VIEW` is carried through `ADD_TO_CART`, `CHECKOUT` and `PURCHASE`. A session whose purchase names a product it never viewed makes the funnel unmeasurable.
- **One RNG per session.** Session `i` draws from `"<seed>:session:<i>"` rather than from a single RNG advanced across the run, so raising `--sessions` from 1000 to 5000 leaves the first 1000 sessions bit-identical. That is what keeps a growing test set debuggable.
- **Simulated time, never wall-clock time.** The run's start instant comes from the config or `--start`, so the output does not depend on when it was run.
- **Output is sorted by `event_time`.** The file is chronological, with sessions interleaved, exactly as a real stream arrives. Later steps inject out-of-order events deliberately and measure the rate; that is only meaningful against an ordered baseline.
