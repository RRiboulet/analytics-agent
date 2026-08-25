"""Tests for the Olist database structure (M1).

Two kinds of tests:
* static tests that inspect the version-controlled init SQL/CSVs and need no
  live database;
* integration tests that run against a reachable PostgreSQL instance and skip
  themselves when one is not available.
"""

import re
from pathlib import Path

import pytest

from app.config import get_settings
from app.data_sources.postgres import PostgresClient

PROJECT = Path(__file__).resolve().parents[1]
INIT_DIR = PROJECT / "db" / "init"
DATA_DIR = PROJECT / "data" / "olist"

SOURCE_ROW_COUNTS = {
    "olist_customers_dataset.csv": 99441,
    "olist_geolocation_dataset.csv": 1000163,
    "olist_order_items_dataset.csv": 112650,
    "olist_order_payments_dataset.csv": 103886,
    "olist_order_reviews_dataset.csv": 99224,
    "olist_orders_dataset.csv": 99441,
    "olist_products_dataset.csv": 32951,
    "olist_sellers_dataset.csv": 3095,
    "product_category_name_translation.csv": 71,
}

EXPECTED_TABLES = {
    "customers",
    "sellers",
    "products",
    "product_category_translation",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
    "geolocation",
}


# ---------------------------------------------------------------------------
# Static tests (no live database)
# ---------------------------------------------------------------------------


def _schema_sql() -> str:
    return (INIT_DIR / "01_schema.sql").read_text()


def _load_sql() -> str:
    return (INIT_DIR / "02_load.sql").read_text()


def test_all_source_csvs_are_present() -> None:
    for name in SOURCE_ROW_COUNTS:
        assert (DATA_DIR / name).is_file(), f"missing {name}"


def test_schema_creates_all_expected_tables() -> None:
    sql = _schema_sql()
    for table in EXPECTED_TABLES:
        assert re.search(rf"CREATE TABLE {table}\s*\(", sql), f"missing CREATE TABLE {table}"


def test_load_script_loads_every_source_csv() -> None:
    sql = _load_sql()
    for name in SOURCE_ROW_COUNTS:
        assert name in sql, f"load script does not reference {name}"


def test_readonly_role_grants_select_only() -> None:
    grant = (INIT_DIR / "03_readonly_role.sql").read_text()
    assert re.search(r"GRANT SELECT ON ALL TABLES", grant)
    # The role must never be granted write/DDL privileges.
    for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
        assert not re.search(rf"GRANT {word}\b", grant), f"readonly role grants {word}"


# ---------------------------------------------------------------------------
# Integration tests (skip when no live database is reachable)
# ---------------------------------------------------------------------------


async def _connect(client: PostgresClient) -> bool:
    try:
        await client.connect()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_expected_tables_present_in_live_db() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 5.0, 100)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        tables = set(await c.list_tables())
        assert EXPECTED_TABLES <= tables
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_source_csv_row_counts_round_trip() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 30.0, 5_000_000)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        table_query = {
            "customers": "olist_customers_dataset.csv",
            "geolocation": "olist_geolocation_dataset.csv",
            "order_items": "olist_order_items_dataset.csv",
            "order_payments": "olist_order_payments_dataset.csv",
            "order_reviews": "olist_order_reviews_dataset.csv",
            "orders": "olist_orders_dataset.csv",
            "products": "olist_products_dataset.csv",
            "sellers": "olist_sellers_dataset.csv",
        }
        for table_name, csv_name in table_query.items():
            rows = await c.fetch_many(f"SELECT count(*) AS n FROM {table_name}")
            assert rows[0]["n"] == SOURCE_ROW_COUNTS[csv_name], (
                f"row count mismatch in {table_name}"
            )
        # category translation has 2 entries added beyond the source file.
        rows = await c.fetch_many("SELECT count(*) AS n FROM product_category_translation")
        assert rows[0]["n"] == SOURCE_ROW_COUNTS["product_category_name_translation.csv"] + 2
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_readonly_cannot_write() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 5.0, 100)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        with pytest.raises(Exception):
            await c.fetch_many("DELETE FROM products")
    finally:
        await c.close()
