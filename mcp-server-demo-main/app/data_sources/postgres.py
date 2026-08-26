import json
from collections.abc import Sequence
from typing import Any

import asyncpg


def _format_vector(vector: Sequence[float]) -> str:
    """Format a Python list of floats as a pgvector literal."""
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def asyncpg_to_json(value: Any) -> str:
    """Serialize a Python object to a JSON string for a jsonb column."""
    return json.dumps(value, default=str)


class PostgresClient:
    # pgvector infrastructure tables that are not part of the analytical schema
    # and must not surface to the analytics agent as queryable sources.
    INTERNAL_TABLES = frozenset({"metadata_documents"})

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
        return [
            str(row["table_name"])
            for row in rows
            if str(row["table_name"]) not in self.INTERNAL_TABLES
        ]

    async def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        if table_name in self.INTERNAL_TABLES:
            return []
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

    # ------------------------------------------------------------------
    # Analytics discovery helpers (M2)
    # ------------------------------------------------------------------

    @staticmethod
    def _quote_ident(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @staticmethod
    def _quote_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    async def _table_exists(self, table_name: str) -> bool:
        if table_name in self.INTERNAL_TABLES:
            return False
        query = """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = $1
        """
        return (
            await self._require_pool().fetchval(
                query, table_name, timeout=self.query_timeout_seconds
            )
            is not None
        )

    async def _column_info(self, table_name: str, column_name: str) -> dict[str, Any] | None:
        if table_name in self.INTERNAL_TABLES:
            return None
        query = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
        """
        rows = await self._require_pool().fetch(
            query, table_name, column_name, timeout=self.query_timeout_seconds
        )
        return dict(rows[0]) if rows else None

    async def get_relationships(self) -> list[dict[str, Any]]:
        """Return the foreign key graph of the public schema."""
        query = """
            SELECT
                child.relname AS child_table,
                child_att.attname AS child_column,
                parent.relname AS parent_table,
                parent_att.attname AS parent_column,
                con.conname AS constraint_name
            FROM pg_constraint con
            JOIN pg_class child ON con.conrelid = child.oid
            JOIN pg_class parent ON con.confrelid = parent.oid
            JOIN pg_namespace ns ON child.relnamespace = ns.oid
            CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                AS cols(child_attnum, parent_attnum)
            JOIN pg_attribute child_att
                ON child_att.attrelid = con.conrelid
                AND child_att.attnum = cols.child_attnum
            JOIN pg_attribute parent_att
                ON parent_att.attrelid = con.confrelid
                AND parent_att.attnum = cols.parent_attnum
            WHERE con.contype = 'f' AND ns.nspname = 'public'
            ORDER BY child.relname, con.conname, cols.child_attnum
        """
        rows = await self._require_pool().fetch(query, timeout=self.query_timeout_seconds)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Metadata / semantic retrieval (M3)
    # ------------------------------------------------------------------

    async def ensure_metadata_schema(self) -> None:
        """Create the pgvector metadata table and index if missing (admin role)."""
        await self._require_pool().execute(
            """
            CREATE TABLE IF NOT EXISTS metadata_documents (
                id           TEXT PRIMARY KEY,
                entity_type  TEXT NOT NULL,
                entity_id    TEXT NOT NULL,
                title        TEXT NOT NULL,
                content      TEXT NOT NULL,
                doc_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                embedding    VECTOR(384) NOT NULL
            )
            """
        )
        await self._require_pool().execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metadata_documents_embedding
                ON metadata_documents USING hnsw (embedding vector_cosine_ops)
            """
        )

    async def replace_metadata_documents(self, documents: list[dict[str, Any]]) -> int:
        """Atomically replace all metadata documents with `documents` (admin role)."""
        if not documents:
            return 0
        async with self._require_pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM metadata_documents")
                for doc in documents:
                    await conn.execute(
                        """
                        INSERT INTO metadata_documents
                            (id, entity_type, entity_id, title, content, doc_metadata, embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        doc["id"],
                        doc["entity_type"],
                        doc["entity_id"],
                        doc["title"],
                        doc["content"],
                        asyncpg_to_json(doc.get("doc_metadata") or {}),
                        _format_vector(doc["embedding"]),
                    )
        return len(documents)

    async def search_metadata(self, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        """Return the most cosine-similar metadata documents (read-only role)."""
        rows = await self._require_pool().fetch(
            """
            SELECT
                id, entity_type, entity_id, title, content, doc_metadata,
                1 - (embedding <=> $1::vector) AS similarity
            FROM metadata_documents
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            _format_vector(query_vector),
            limit,
            timeout=self.query_timeout_seconds,
        )
        return [dict(row) for row in rows]

    async def get_sample_rows(self, table_name: str, limit: int) -> list[dict[str, Any]] | None:
        """Return up to `limit` arbitrary rows from a public table, or None if unknown."""
        if not await self._table_exists(table_name):
            return None
        query = f"SELECT * FROM {self._quote_ident(table_name)} LIMIT {limit}"
        rows = await self._require_pool().fetch(query, timeout=self.query_timeout_seconds)
        return [dict(row) for row in rows]

    async def get_table_statistics(self) -> list[dict[str, Any]]:
        """Return the exact row count for every public table."""
        tables = await self.list_tables()
        if not tables:
            return []
        parts = [
            f"SELECT {self._quote_literal(table)} AS table_name, count(*) AS row_count "
            f"FROM {self._quote_ident(table)}"
            for table in tables
        ]
        query = " UNION ALL ".join(parts)
        rows = await self._require_pool().fetch(query, timeout=self.query_timeout_seconds)
        return [dict(row) for row in rows]

    async def get_column_statistics(
        self, table_name: str, column_name: str
    ) -> dict[str, Any] | None:
        """Return distribution statistics for one column, or None if unknown."""
        info = await self._column_info(table_name, column_name)
        if info is None:
            return None
        column = self._quote_ident(column_name)
        query = f"""
            SELECT
                count(*) AS total_rows,
                count(DISTINCT {column}) FILTER (WHERE {column} IS NOT NULL) AS distinct_count,
                count(*) - count({column}) AS null_count,
                min({column}) AS min_value,
                max({column}) AS max_value
            FROM {self._quote_ident(table_name)}
        """
        row = await self._require_pool().fetchrow(query, timeout=self.query_timeout_seconds)
        result = dict(row)
        result["data_type"] = info["data_type"]
        return result
