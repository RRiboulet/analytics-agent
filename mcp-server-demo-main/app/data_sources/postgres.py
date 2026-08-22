from collections.abc import Sequence
from typing import Any

import asyncpg


class PostgresClient:
    def __init__(self, database_url: str, query_timeout_seconds: float, max_rows: int) -> None:
        self.database_url = database_url
        self.query_timeout_seconds = query_timeout_seconds
        self.max_rows = max_rows
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=self.query_timeout_seconds,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("The PostgreSQL client is not connected.")
        return self.pool

    async def list_tables(self) -> list[str]:
        query = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        rows = await self._require_pool().fetch(query, timeout=self.query_timeout_seconds)
        return [str(row["table_name"]) for row in rows]

    async def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        query = """
            SELECT column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
        """
        rows = await self._require_pool().fetch(query, table_name, timeout=self.query_timeout_seconds)
        return [dict(row) for row in rows]

    async def query(self, sql: str) -> list[dict[str, Any]]:
        rows = await self._require_pool().fetch(sql, timeout=self.query_timeout_seconds)
        return [dict(row) for row in rows[: self.max_rows]]

    async def healthcheck(self) -> bool:
        await self._require_pool().fetchval("SELECT 1", timeout=self.query_timeout_seconds)
        return True

    async def fetch_many(self, sql: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
        rows = await self._require_pool().fetch(sql, *parameters, timeout=self.query_timeout_seconds)
        return [dict(row) for row in rows[: self.max_rows]]
