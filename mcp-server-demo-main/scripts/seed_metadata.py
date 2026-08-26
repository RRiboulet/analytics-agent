"""Reproducibly build and load the pgvector metadata documents.

Usage (from the project home, after `docker compose up -d postgres`):

    uv run python -m scripts.seed_metadata

This connects with the ADMIN role, (re)creates the metadata schema, builds the
metadata documents, embeds them locally via fastembed, and atomically replaces
the metadata_documents table. Running it again refreshes the index in place.
"""

import asyncio

from app.config import get_settings
from app.data_sources.postgres import PostgresClient
from app.embedder import get_embedder
from app.metadata import build_metadata_documents


async def _main() -> None:
    settings = get_settings()
    admin = PostgresClient(
        database_url=settings.admin_database_url,
        query_timeout_seconds=settings.query_timeout_seconds,
        max_rows=settings.max_rows,
    )
    await admin.connect()
    try:
        await admin.ensure_metadata_schema()

        documents = await build_metadata_documents(admin)
        embedder = get_embedder()
        for doc in documents:
            doc["embedding"] = embedder.embed_query(doc["content"])

        count = await admin.replace_metadata_documents(documents)
        print(f"Seeded {count} metadata documents.")
    finally:
        await admin.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
