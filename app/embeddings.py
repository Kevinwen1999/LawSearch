from functools import lru_cache

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    if torch.cuda.is_available():
        model = SentenceTransformer(
            settings.embedding_model,
            device=settings.embedding_device,
            model_kwargs={"dtype": torch.float16},
        )
    else:
        model = SentenceTransformer(settings.embedding_model, device="cpu")
    model.max_seq_length = settings.embedding_max_seq_length
    return model


def embed(texts: list[str]) -> np.ndarray:
    return get_model().encode(
        texts,
        normalize_embeddings=True,
        batch_size=settings.embedding_batch_size,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float16)
