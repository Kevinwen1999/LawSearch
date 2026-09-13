import threading
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


# The API serves requests from a thread pool; serialize access to the shared GPU model.
_encode_lock = threading.Lock()


def embed(texts: list[str]) -> np.ndarray:
    model = get_model()
    with _encode_lock:
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=settings.embedding_batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    return vectors.astype(np.float16)
