import threading
from functools import lru_cache

import torch
from sentence_transformers import CrossEncoder

from app.config import settings


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    if torch.cuda.is_available():
        return CrossEncoder(
            settings.reranker_model,
            device=settings.embedding_device,
            max_length=settings.reranker_max_length,
            model_kwargs={"dtype": torch.float16},
        )
    return CrossEncoder(settings.reranker_model, device="cpu", max_length=settings.reranker_max_length)


_predict_lock = threading.Lock()


def score_pairs(query: str, passages: list[str]) -> list[float]:
    """Cross-encoder relevance of each passage to the query (higher is more relevant)."""
    if not passages:
        return []
    model = get_reranker()
    with _predict_lock:
        # Raw logits: the model's default sigmoid saturates near 1.0 in fp16, collapsing the
        # ordering of strong candidates into ties.
        scores = model.predict(
            [(query, p) for p in passages],
            batch_size=32,
            show_progress_bar=False,
            activation_fn=torch.nn.Identity(),
        )
    return [float(s) for s in scores]
