"""Live-database validation of the M6 evaluation benchmark.

Every reference SQL statement must execute against the real Olist database
through the read-only safety boundary, return at most ``max_rows`` rows (the
same bound the MCP ``query`` tool applies to agent SQL, so neither side of the
comparison is truncated), and return a non-empty result set. Skips itself when
PostgreSQL is not reachable.
"""

import asyncio

import pytest

from app.config import get_settings
from app.data_sources.postgres import PostgresClient
from app.evaluation.dataset import load_dataset
from app.sql_safety import validate_and_bound_query

DATASET_PATH = "data/evaluation/olist_v1.yaml"


async def _connect(client: PostgresClient, retries: int = 10, delay: float = 1.0) -> bool:
    """Connect with retries (fresh-init temporary-server window, see M1)."""
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
async def test_benchmark_reference_sql_executes_on_live_db() -> None:
    settings = get_settings()
    cases = load_dataset(DATASET_PATH)
    c = PostgresClient(settings.database_url, 30.0, settings.max_rows)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        assert len(cases) == 30
        for case in cases:
            bounded = validate_and_bound_query(case.reference_sql, max_rows=settings.max_rows)
            rows = await c.fetch_many(bounded)
            assert 1 <= len(rows) <= settings.max_rows, (
                f"case {case.case_id}: reference SQL returned {len(rows)} rows"
            )
    finally:
        await c.close()
