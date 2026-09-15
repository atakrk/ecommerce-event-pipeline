"""Generate product reference data -> data/products.jsonl"""
import math
import statistics

from reference.common import (
    base_arg_parser,
    ensure_writable,
    load_config,
    output_path,
    rng_for,
    weighted_choice,
    write_jsonl,
)

OUTPUT_FILE = "products.jsonl"


def generate_products(config):
    products_cfg = config["products"]
    rng = rng_for(config, "products")

    categories = products_cfg["categories"]
    category_weights = {name: c["weight"] for name, c in categories.items()}

    for i in range(1, products_cfg["count"] + 1):
        category = weighted_choice(rng, category_weights)
        bounds = categories[category]
        # Log-uniform: cheap products are more common than expensive ones, as in real catalogs.
        log_price = rng.uniform(math.log(bounds["price_min"]), math.log(bounds["price_max"]))
        yield {
            "product_id": f"P{i:05d}",
            "category": category,
            "price": round(math.exp(log_price), 2),
        }


def main():
    args = base_arg_parser(__doc__).parse_args()
    config = load_config(args.config)
    path = output_path(config, OUTPUT_FILE)
    if not ensure_writable(path, args.force):
        return

    products = list(generate_products(config))
    write_jsonl(path, products)

    print(f"{len(products)} products -> {path}")
    print(f"  {'category':<12} {'count':>6} {'min':>9} {'median':>9} {'max':>9}")
    for category in config["products"]["categories"]:
        prices = [p["price"] for p in products if p["category"] == category]
        if not prices:
            continue
        print(
            f"  {category:<12} {len(prices):>6} {min(prices):>9.2f}"
            f" {statistics.median(prices):>9.2f} {max(prices):>9.2f}"
        )


if __name__ == "__main__":
    main()
