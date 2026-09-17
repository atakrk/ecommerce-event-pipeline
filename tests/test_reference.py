"""Tests for the user and product reference data generators.

uv run pytest tests/test_reference.py
"""

from pathlib import Path

import pytest
import yaml

from reference.generate_products import generate_products
from reference.generate_users import generate_users

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "config.yaml"


@pytest.fixture
def config():
    return yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))


def test_users_have_sequential_ids_and_every_field(config):
    users = list(generate_users(config))

    assert len(users) == config["users"]["count"]
    assert [user["user_id"] for user in users[:3]] == ["U000001", "U000002", "U000003"]
    assert set(users[0]) == {"user_id", "country", "city", "device_type", "created_at"}


def test_users_draw_only_configured_countries_cities_and_devices(config):
    countries = config["users"]["countries"]
    for user in generate_users(config):
        assert user["city"] in countries[user["country"]]["cities"]
        assert user["device_type"] in config["users"]["device_types"]


def test_user_created_at_stays_inside_the_inclusive_date_range(config):
    config["users"]["created_at_range"] = {"start": "2025-03-01", "end": "2025-03-01"}

    created = [user["created_at"] for user in generate_users(config)]

    # A one day range: every instant falls on that day, up to its last second.
    assert all("2025-03-01T00:00:00Z" <= moment <= "2025-03-01T23:59:59Z" for moment in created)


def test_user_created_at_accepts_yaml_date_objects(config):
    # Unquoted YAML dates arrive as datetime.date, not str.
    config["users"]["created_at_range"] = yaml.safe_load("{start: 2025-03-01, end: 2025-03-02}")
    created = [user["created_at"] for user in generate_users(config)]
    assert all(moment.startswith(("2025-03-01", "2025-03-02")) for moment in created)


def test_products_have_sequential_ids_and_every_field(config):
    products = list(generate_products(config))

    assert len(products) == config["products"]["count"]
    assert [product["product_id"] for product in products[:2]] == ["P00001", "P00002"]
    assert set(products[0]) == {"product_id", "category", "price"}


def test_product_prices_stay_inside_their_category_bounds(config):
    categories = config["products"]["categories"]
    for product in generate_products(config):
        bounds = categories[product["category"]]
        assert bounds["price_min"] <= product["price"] <= bounds["price_max"]
        assert round(product["price"], 2) == product["price"]


def test_users_and_products_use_independent_rngs(config):
    users_before = list(generate_users(config))
    config["products"]["count"] = 7

    list(generate_products(config))

    assert list(generate_users(config)) == users_before


def test_the_same_config_gives_the_same_reference_data(config):
    assert list(generate_users(config)) == list(generate_users(config))
    assert list(generate_products(config)) == list(generate_products(config))
