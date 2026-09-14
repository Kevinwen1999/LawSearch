from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://lawsearch:lawsearch@localhost:5432/lawsearch"
    # Downloaded source data (e.g. the Justice Laws XML clone). Point at a roomy drive.
    data_dir: str = "data"
    db_pool_size: int = 8
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_device: str = "cuda:0"
    embedding_batch_size: int = 64
    # Chunks are capped at 1500 chars (~400 tokens), so this only affects long scenario
    # queries, which a 512-token cap was truncating (losing the question at the end).
    embedding_max_seq_length: int = 1024
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    # Query + passage share this budget; at 512 a long scenario squeezed out the passage.
    reranker_max_length: int = 1024

    # "claude-cli" runs `claude -p` on the CLI's own login (fine for local testing);
    # "api" uses the Anthropic SDK with ANTHROPIC_API_KEY. Same prompt, schema and cache.
    filac_backend: Literal["claude-cli", "api"] = "claude-cli"
    filac_model: str = "claude-opus-5"
    filac_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    claude_cli_path: str = "claude"
    llm_timeout_seconds: int = 900


settings = Settings()
