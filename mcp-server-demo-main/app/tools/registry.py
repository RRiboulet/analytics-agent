from mcp.server.fastmcp import FastMCP

from app.data_sources.postgres import PostgresClient
from app.tools.describe_factory_table import register as register_describe_factory_table
from app.tools.list_factory_tables import register as register_list_factory_tables
from app.tools.query_factory_data import register as register_query_factory_data


def register_tools(mcp: FastMCP, client: PostgresClient) -> None:
    register_list_factory_tables(mcp, client)
    register_describe_factory_table(mcp, client)
    register_query_factory_data(mcp, client)
