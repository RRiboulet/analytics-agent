-- pgvector semantic metadata layer (M3).
--
-- This runs inside the same PostgreSQL database as the analytical dataset.
-- pgvector is used ONLY for semantic retrieval of database metadata; the
-- analytical data itself remains in the plain relational tables above.
--
-- The extension and table are created by the database admin (olist_admin,
-- the docker image superuser). The analytics MCP server runs as the read-only
-- olist_readonly role, which is granted SELECT below (and automatically for
-- tables created later via the ALTER DEFAULT PRIVILEGES set in 03).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS metadata_documents (
    -- Stable per-entity identity, e.g. "table:orders", "column:orders.order_status",
    -- "relation:order_items.order_id->orders.order_id". Seeding is idempotent.
    id           TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,        -- 'table' | 'column' | 'relation'
    entity_id    TEXT NOT NULL,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    doc_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Fixed dimension matches app/config.py EMBEDDING_DIMENSIONS (384).
    embedding    VECTOR(384) NOT NULL
);

-- Approximate nearest-neighbour search over cosine similarity. The dimension
-- (384) matches the fixed embedding model in app/config.py.
CREATE INDEX IF NOT EXISTS idx_metadata_documents_embedding
    ON metadata_documents USING hnsw (embedding vector_cosine_ops);

GRANT SELECT ON metadata_documents TO olist_readonly;