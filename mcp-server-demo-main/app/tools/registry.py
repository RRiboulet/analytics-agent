from mcp.server.fastmcp import FastMCP

from app.data_sources.postgres import PostgresClient
from app.tools.describe_table import register as register_describe_table
from app.tools.list_tables import register as register_list_tables
from app.tools.query import register as register_query


def register_tools(mcp: FastMCP, client: PostgresClient) -> None:
    register_list_tables(mcp, client)
    register_describe_table(mcp, client)
    register_query(mcp, client)
