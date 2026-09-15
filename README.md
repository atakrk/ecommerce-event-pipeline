# ecommerce-event-pipeline

A data pipeline that synthetically generates user behavior events for an e-commerce site (product views, add-to-cart, checkout, purchase) and processes them end to end. The goal is to build event processing, validation, and analytics step by step on top of a realistic but controllable data source.

## Roadmap

- [x] **Step 1 — Repo and reference data:** user and product tables
- [ ] **Step 2 — Event generation:** read the reference data and generate funnel events
- [ ] _Next steps — TBD_

## Setup

Requirements: Python 3.9+

```bash
make venv        # creates .venv and installs dependencies
make reference   # generates data/users.jsonl and data/products.jsonl
```

Other commands:

| Command                | What it does                                       |
| ---------------------- | -------------------------------------------------- |
| `make reference`       | Generates reference data; skips files that exist   |
| `make reference-force` | Regenerates, overwriting existing files            |
| `make clean-data`      | Deletes `data/*.jsonl`                             |

The scripts can also be run directly:

```bash
.venv/bin/python -m reference.generate_users [--config config.yaml] [--force]
.venv/bin/python -m reference.generate_products [--config config.yaml] [--force]
```

## Configuration

All parameters live in [`config.yaml`](config.yaml): `seed`, user/product counts, country–city and device distributions, category weights and price ranges, and funnel conversion rates.

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

## Design decisions

- **Reference data is generated once.** In a real system, users and products exist before any events. The event generator only reads these files; the scripts never overwrite an existing file without `--force`.
- **Small scale (10k users / 1k products).** Enough to learn the pipeline logic; bigger files only slow down iteration.
- **Deterministic generation.** Same `seed` → byte-identical files. The user and product generators use separately derived seeds, so changing one does not affect the other. That's why `data/` is not committed.
- **Atomic writes.** Files are written to a `.tmp` file first and then moved into place, so an interrupted run never leaves a corrupt file behind.
