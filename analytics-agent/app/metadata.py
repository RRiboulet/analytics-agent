"""Build plain-language metadata documents from the schema + seed.

Each document is a self-contained, searchable piece of database metadata for
one entity: a table, a column, or a relationship. Documents combine auto-
generated schema facts (columns, data types, foreign keys) with curated
business meaning and retrieval keywords from ``metadata_seed.json`` so a
natural-language question can embed and retrieve the relevant tables/columns/
relationships semantically, without sending the whole schema to the LLM.
"""

import json
from pathlib import Path
from typing import Any

from app.data_sources.postgres import PostgresClient

_SEED_PATH = Path(__file__).with_name("metadata_seed.json")

TABLE = "table"
COLUMN = "column"
RELATION = "relation"

# Internal pgvector infrastructure that is not part of the analytical dataset
# and must not be suggested as a source table.
_SKIP_TABLES = frozenset({"metadata_documents"})


def _load_seed() -> dict[str, Any]:
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _entry_text(entry: Any, default: str) -> tuple[str, list[str]]:
    """Normalize a seed entry (string description or {description, keywords})."""
    if isinstance(entry, str):
        return entry, []
    if isinstance(entry, dict):
        return entry.get("description", default), list(entry.get("keywords", []) or [])
    return default, []


def _keywords_suffix(keywords: list[str]) -> str:
    if not keywords:
        return ""
    return " Keywords: " + ", ".join(keywords) + "."


def _friendly_type(data_type: str) -> str:
    return data_type.replace("_", " ")


def _table_document(table: str, description: str, keywords: list[str]) -> dict[str, Any]:
    return {
        "id": f"table:{table}",
        "entity_type": TABLE,
        "entity_id": table,
        "title": f"Table {table}",
        "content": f"Table {table}: {description}{_keywords_suffix(keywords)}".strip(),
        "doc_metadata": {"kind": TABLE},
    }


def _column_document(
    table: str,
    table_desc: str,
    col_name: str,
    data_type: str,
    description: str,
    keywords: list[str],
) -> dict[str, Any]:
    entity = f"{table}.{col_name}"
    return {
        "id": f"column:{entity}",
        "entity_type": COLUMN,
        "entity_id": entity,
        "title": f"Column {entity}",
        "content": (
            f"Column {entity} in table {table} ({table_desc}) has type "
            f"{_friendly_type(data_type)}. {description}{_keywords_suffix(keywords)}".strip()
        ),
        "doc_metadata": {"kind": COLUMN},
    }


def _relationship_document(rel: dict[str, Any]) -> dict[str, Any]:
    child = rel["child_table"]
    parent = rel["parent_table"]
    label = f"{child}.{rel['child_column']} -> {parent}.{rel['parent_column']}"
    return {
        "id": f"relation:{label}",
        "entity_type": RELATION,
        "entity_id": label,
        "title": f"Relationship {label}",
        "content": (
            f"{child} joins to {parent} on {rel['child_column']} = "
            f"{parent}.{rel['parent_column']}. Use this to join {child} and {parent}."
        ),
        "doc_metadata": {"kind": RELATION},
    }


async def build_metadata_documents(client: PostgresClient) -> list[dict[str, Any]]:
    """Generate metadata documents from schema facts merged with curated seed."""
    seed = _load_seed()
    table_seeds: dict[str, Any] = seed.get("tables", {})
    column_seeds: dict[str, Any] = seed.get("columns", {})
    default_table = "An analytical table in the e-commerce dataset."

    docs: list[dict[str, Any]] = []
    for table in await client.list_tables():
        if table in _SKIP_TABLES:
            continue
        table_desc, table_keywords = _entry_text(table_seeds.get(table), default_table)
        docs.append(_table_document(table, table_desc, table_keywords))
        for col in await client.describe_table(table):
            col_desc, col_keywords = _entry_text(
                column_seeds.get(f"{table}.{col['column_name']}"), ""
            )
            docs.append(
                _column_document(
                    table, table_desc, col["column_name"], col["data_type"], col_desc, col_keywords
                )
            )

    for rel in await client.get_relationships():
        docs.append(_relationship_document(rel))

    return docs
