from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://factory_readonly:factory_readonly@localhost:5432/factory",
        validation_alias="DATABASE_URL",
    )
    mcp_host: str = Field(default="0.0.0.0", validation_alias="MCP_HOST")
    mcp_port: int = Field(default=8000, validation_alias="MCP_PORT", ge=1, le=65535)
    query_timeout_seconds: float = Field(default=10.0, validation_alias="QUERY_TIMEOUT_SECONDS", gt=0)
    max_rows: int = Field(default=100, validation_alias="MAX_ROWS", ge=1, le=1000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
