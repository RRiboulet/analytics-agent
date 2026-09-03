from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env resolved from the project root (this file lives in app/), not the
# CWD: the package is installed in the venv, so the app runs correctly from
# any directory, and a CWD-relative env_file would silently ignore the
# project configuration when launched elsewhere.
_PROJECT_ENV = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ENV, env_file_encoding="utf-8", extra="ignore"
    )

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
        # llama.cpp serves the OpenAI-compatible API on its HTTP port (8080 by
        # default). host.docker.internal lets the devcontainer reach the host's
        # llama-server; the server must listen on a container-reachable interface.
        default="http://host.docker.internal:8080/v1",
        validation_alias="LLM_BASE_URL",
    )
    # llama.cpp exposes the model under the name given by --alias, not the GGUF
    # filename. The server below is started with --alias gemma-4.
    llm_model: str = Field(default="gemma-4", validation_alias="LLM_MODEL")
    # Which LLM backend the agent uses. "llamacpp" (default) keeps the local
    # OpenAI-compatible server configuration (LLM_BASE_URL/LLM_MODEL, no auth);
    # "openrouter" switches to the hosted OpenRouter API, which requires
    # OPENROUTER_API_KEY and uses OPENROUTER_MODEL. Both configurations stay
    # in .env simultaneously; the provider selects which one is active.
    llm_provider: Literal["llamacpp", "openrouter"] = Field(
        default="llamacpp", validation_alias="LLM_PROVIDER"
    )
    # OpenRouter serves an OpenAI-compatible API at this base URL.
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    # Secret: read from the environment/.env only; never logged or echoed in
    # error messages. Required iff llm_provider is "openrouter".
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    # OpenRouter model id (e.g. "openai/gpt-4o-mini",
    # "meta-llama/llama-3.3-70b-instruct").
    openrouter_model: str = Field(default="openai/gpt-4o-mini", validation_alias="OPENROUTER_MODEL")
    llm_timeout_seconds: float = Field(default=300.0, validation_alias="LLM_TIMEOUT_SECONDS", gt=0)
    # Cap per-generation output tokens. This matters on servers started with
    # --reasoning on, where the model spends tokens on chain-of-thought before
    # emitting the final SQL/answer; without a cap the reasoning phase can crowd
    # out the final output and yield empty/truncated text.
    llm_max_tokens: int | None = Field(
        default=4096, validation_alias="LLM_MAX_TOKENS", ge=1, le=32768
    )
    # Separate, tighter cap for the final answer step. The SQL step may need
    # room for chain-of-thought; the answer step only needs a concise
    # evidence-based summary, and a small cap bounds the worst-case wall time
    # on slow local models.
    llm_answer_max_tokens: int | None = Field(
        default=512, validation_alias="LLM_ANSWER_MAX_TOKENS", ge=1, le=32768
    )
    # Maximum regeneration attempts before the agent fails out (no infinite
    # loops when SQL stays invalid or errors).
    agent_max_attempts: int = Field(default=3, validation_alias="AGENT_MAX_ATTEMPTS", ge=1, le=10)

    # ------------------------------------------------------------------
    # Analytics manager (M7)
    # ------------------------------------------------------------------

    # Bounded retry budget for the manager's retryable model calls
    # (decompose / synthesize). Shared by both stages so it stays a single
    # hard bound (D009 fixed pipeline).
    manager_max_attempts: int = Field(
        default=2, validation_alias="MANAGER_MAX_ATTEMPTS", ge=1, le=10
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
