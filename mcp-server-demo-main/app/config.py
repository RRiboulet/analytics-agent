from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://olist_readonly:olist_readonly@localhost:5432/olist",
        validation_alias="DATABASE_URL",
    )
    mcp_host: str = Field(default="0.0.0.0", validation_alias="MCP_HOST")
    mcp_port: int = Field(default=8000, validation_alias="MCP_PORT", ge=1, le=65535)
    query_timeout_seconds: float = Field(
        default=10.0, validation_alias="QUERY_TIMEOUT_SECONDS", gt=0
    )
    max_rows: int = Field(default=100, validation_alias="MAX_ROWS", ge=1, le=1000)

    # ------------------------------------------------------------------
    # Metadata / semantic retrieval (M3)
    # ------------------------------------------------------------------

    # Programming-language-administration URL used only by the reproducible
    # metadata seeding script and tests. The running MCP server stays on the
    # read-only olist_readonly role; it never uses these credentials.
    admin_database_url: str = Field(
        default="postgresql://olist_admin:olist_admin@localhost:5432/olist",
        validation_alias="ADMIN_DATABASE_URL",
    )
    # fastembed model served locally via ONNX (no external API, no Ollama).
    embedding_model_name: str = Field(
        default="BAAI/bge-small-en-v1.5", validation_alias="EMBEDDING_MODEL_NAME"
    )
    embedding_dimensions: int = Field(default=384, validation_alias="EMBEDDING_DIMENSIONS", ge=1)
    # Where fastembed caches the downloaded ONNX weights (deterministic, not /tmp).
    embedding_cache_dir: str | None = Field(default=None, validation_alias="EMBEDDING_CACHE_DIR")
    # Default number of semantic-search hits returned by search_metadata.
    metadata_search_top_k: int = Field(
        default=5, validation_alias="METADATA_SEARCH_TOP_K", ge=1, le=50
    )

    # ------------------------------------------------------------------
    # Analytics agent (M4)
    # ------------------------------------------------------------------

    # Streamable HTTP endpoint of the MCP server the agent consumes. The
    # agent is a standalone client and never reaches PostgreSQL directly.
    mcp_url: str = Field(default="http://localhost:8000/mcp", validation_alias="MCP_URL")
    # OpenAI-compatible chat-completions endpoint of the local LLM. The
    # default points at the host (devcontainer peers through
    # host.docker.internal); the model server must listen on a
    # container-reachable interface.
    llm_base_url: str = Field(
        default="http://host.docker.internal:11434/v1", validation_alias="LLM_BASE_URL"
    )
    llm_model: str = Field(default="gemma-4-E4B", validation_alias="LLM_MODEL")
    llm_timeout_seconds: float = Field(default=120.0, validation_alias="LLM_TIMEOUT_SECONDS", gt=0)
    # Maximum regeneration attempts before the agent fails out (no infinite
    # loops when SQL stays invalid or errors).
    agent_max_attempts: int = Field(default=3, validation_alias="AGENT_MAX_ATTEMPTS", ge=1, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
