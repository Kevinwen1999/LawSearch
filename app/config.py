from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://lawsearch:lawsearch@localhost:5432/lawsearch"
    db_pool_size: int = 8
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_device: str = "cuda:0"
    embedding_batch_size: int = 64
    # Chunks are capped at 1500 chars (~400 tokens); this bounds GPU memory per batch.
    embedding_max_seq_length: int = 512


settings = Settings()
