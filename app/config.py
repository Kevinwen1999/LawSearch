from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Backend = Literal["claude-cli", "api", "lmstudio"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


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
    # "api" uses the Anthropic SDK with ANTHROPIC_API_KEY; "lmstudio" uses a local model served
    # by LM Studio's OpenAI-compatible endpoint. Same prompt, schema and cache across all three.
    filac_backend: Backend = "claude-cli"
    filac_model: str = "claude-opus-5"
    filac_effort: Effort = "high"
    # Same backend/model by default, so this is a no-op unless filac_backend is changed to
    # lmstudio — then a down local server falls back to cloud instead of failing the brief.
    filac_fallback_backend: Backend = "claude-cli"
    filac_fallback_model: str = "claude-opus-5"
    filac_fallback_effort: Effort = "high"
    claude_cli_path: str = "claude"
    llm_timeout_seconds: int = 900

    # LM Studio's local OpenAI-compatible server. filac_model must match a model id from
    # GET {lmstudio_base_url}/models (e.g. "qwen/qwen3.8-27b") when filac_backend=lmstudio.
    lmstudio_base_url: str = "http://localhost:1234/v1"
    # Shared budget for the model's own reasoning plus the JSON answer; reasoning-capable local
    # models can burn most of this on <reasoning_content> before ever emitting the answer.
    lmstudio_max_tokens: int = 8000

    # Scenario fingerprinting (Phase 6). Scenarios are short, so the local model's context and
    # token-spend risk (real for FILAC on long decisions) aren't a concern here; default to the
    # free local backend. If LM Studio isn't running (or errors), fall back to Sonnet via
    # claude-cli — cheap cloud option, the default before local was proven out.
    fingerprint_backend: Backend = "lmstudio"
    fingerprint_model: str = "qwen/qwen3.8-27b"
    fingerprint_effort: Effort = "low"
    fingerprint_fallback_backend: Backend = "claude-cli"
    fingerprint_fallback_model: str = "claude-sonnet-5"
    fingerprint_fallback_effort: Effort = "low"

    # Scenario file uploads: original file goes to MinIO keyed by content hash; extracted text
    # is never logged.
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "scenario-uploads"
    minio_secure: bool = False
    # Image-only PDFs (scanned filings) fall back to OCR, page by page; cap so a huge upload
    # can't tie up the GPU-shared OCR pass for minutes.
    ocr_max_pages: int = 25


settings = Settings()
