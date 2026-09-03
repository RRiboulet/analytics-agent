from mcp.server.fastmcp import FastMCP

from app.data_sources.postgres import PostgresClient
from app.embedder import MetadataEmbedder
from app.tools.describe_table import register as register_describe_table
from app.tools.get_column_statistics import register as register_get_column_statistics
from app.tools.get_relationships import register as register_get_relationships
from app.tools.get_sample_rows import register as register_get_sample_rows
from app.tools.get_table_statistics import register as register_get_table_statistics
from app.tools.list_tables import register as register_list_tables
from app.tools.query import register as register_query
from app.tools.search_metadata import register as register_search_metadata


def register_tools(mcp: FastMCP, client: PostgresClient, embedder: MetadataEmbedder) -> None:
    register_list_tables(mcp, client)
    register_describe_table(mcp, client)
    register_query(mcp, client)
    register_get_relationships(mcp, client)
    register_get_sample_rows(mcp, client)
    register_get_table_statistics(mcp, client)
    register_get_column_statistics(mcp, client)
    register_search_metadata(mcp, client, embedder)
