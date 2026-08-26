"""Tests for the Olist database structure (M1).

Two kinds of tests:
* static tests that inspect the version-controlled init SQL/CSVs and need no
  live database;
* integration tests that run against a reachable PostgreSQL instance and skip
  themselves when one is not available.
"""

import asyncio
import re
from pathlib import Path

import asyncpg.exceptions
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


async def _connect(client: PostgresClient, retries: int = 10, delay: float = 1.0) -> bool:
    """Connect with retries.

    On a fresh init the Postgres entrypoint runs on a temporary server and then
    restarts the real one; pg_isready can report healthy in that window while
    real connects still fail. Retry before giving up so the integration tests
    do not silently skip on a fresh clone.
    """
    for attempt in range(retries):
        try:
            await client.connect()
            return True
        except Exception:
            if attempt == retries - 1:
                return False
            await asyncio.sleep(delay)
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
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await c.fetch_many("DELETE FROM products")
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# M2 analytics discovery methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relationships_match_schema() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 5.0, 100)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        relationships = await c.get_relationships()
        expected = {
            (
                "products",
                "product_category_name",
                "product_category_translation",
                "product_category_name",
            ),
            ("orders", "customer_id", "customers", "customer_id"),
            ("order_items", "order_id", "orders", "order_id"),
            ("order_items", "product_id", "products", "product_id"),
            ("order_items", "seller_id", "sellers", "seller_id"),
            ("order_payments", "order_id", "orders", "order_id"),
            ("order_reviews", "order_id", "orders", "order_id"),
        }
        actual = {
            (r["child_table"], r["child_column"], r["parent_table"], r["parent_column"])
            for r in relationships
        }
        assert actual == expected
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_sample_rows_are_bounded_and_well_formed() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 5.0, 100)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        rows = await c.get_sample_rows("orders", 3)
        assert rows is not None
        assert len(rows) == 3
        assert {"order_id", "order_status", "order_purchase_timestamp"} <= set(rows[0])
        assert await c.get_sample_rows("does_not_exist", 3) is None
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_table_statistics_are_exact() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 30.0, 5_000_000)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        stats = {s["table_name"]: s["row_count"] for s in await c.get_table_statistics()}
        assert len(stats) == 9
        assert stats["orders"] == 99441
        assert stats["geolocation"] == 1000163
        assert stats["products"] == 32951
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_column_statistics_are_sane() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 30.0, 5_000_000)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        stats = await c.get_column_statistics("orders", "order_status")
        assert stats is not None
        assert stats["total_rows"] == 99441
        assert stats["distinct_count"] == 8
        assert stats["null_count"] == 0
        assert stats["data_type"] == "text"
        assert await c.get_column_statistics("orders", "does_not_exist") is None
        assert await c.get_column_statistics("does_not_exist", "order_id") is None
    finally:
        await c.close()
