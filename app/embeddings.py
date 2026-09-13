from functools import lru_cache

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return SentenceTransformer(settings.embedding_model, device=device)


def embed(texts: list[str]) -> np.ndarray:
    return get_model().encode(
        texts,
        normalize_embeddings=True,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=False,
    )
