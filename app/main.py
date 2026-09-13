from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app import filac, retrieval
from app.config import settings
from app.db import get_pool
from app.embeddings import embed
from app.llm import LLMError
from app.reranker import score_pairs


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load both models and run them once so the first request skips CUDA warm-up.
    embed(["warm-up"])
    score_pairs("warm-up", ["warm-up"])
    pool = get_pool()
    pool.open(wait=True)
    yield
    pool.close()


app = FastAPI(title="LawSearch", version="0.4.0", lifespan=lifespan)

CourtCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,10}$", to_upper=True)]


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=4000)
    k: int = Field(10, ge=1, le=50)
    mode: retrieval.Mode = "hybrid"
    courts: list[CourtCode] | None = Field(None, max_length=30)
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def check_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class PassageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    para_no: int | None
    para_end: int | None
    text: str
    matched_by: list[str]


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    citation: str | None
    citation2: str | None
    style_of_cause: str | None
    court: str | None
    decision_date: date | None
    url: str | None
    language: str | None
    cited_by_count: int
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    graph_rank: int | None
    citing_seeds: int
    rerank_score: float | None
    passages: list[PassageOut]


class SearchResponse(BaseModel):
    query: str
    mode: retrieval.Mode
    results: list[CaseOut]
    timings_ms: dict[str, float]


class FilacOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    prompt_version: str
    model: str
    backend: str
    summary: dict
    verification: dict
    usage: dict
    created_at: datetime


@app.get("/health")
def health() -> dict:
    with get_pool().connection() as conn:
        extensions = dict(
            conn.execute(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_textsearch')"
            ).fetchall()
        )
        cases = conn.execute("SELECT count(*) FROM cases").fetchone()[0]
        chunks = conn.execute(
            "SELECT reltuples::bigint FROM pg_class WHERE relname = 'case_chunks'"
        ).fetchone()[0]
    return {
        "status": "ok",
        "extensions": extensions,
        "cases": cases,
        "chunks_estimate": chunks,
        "filac": {"backend": settings.filac_backend, "model": settings.filac_model},
    }


@app.get("/courts")
def courts() -> list[dict]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT court, count(*) FROM cases GROUP BY court ORDER BY count(*) DESC"
        ).fetchall()
    return [{"court": court, "cases": n} for court, n in rows]


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    with get_pool().connection() as conn:
        result = retrieval.search(
            conn,
            req.query,
            k=req.k,
            mode=req.mode,
            courts=req.courts,
            date_from=req.date_from,
            date_to=req.date_to,
        )
    return SearchResponse(
        query=req.query,
        mode=req.mode,
        results=[CaseOut.model_validate(c) for c in result.cases],
        timings_ms=result.timings_ms,
    )


@app.get("/cases/{case_id}/filac", response_model=FilacOut)
def get_filac(case_id: UUID) -> FilacOut:
    with get_pool().connection() as conn:
        record = filac.get_cached(conn, case_id)
    if record is None:
        raise HTTPException(404, "No FILAC brief has been generated for this case yet")
    return FilacOut.model_validate(record)


@app.post("/cases/{case_id}/filac", response_model=FilacOut)
def create_filac(case_id: UUID, force: bool = False) -> FilacOut:
    """Generate (or return the cached) FILAC brief. Can take minutes for long decisions."""
    try:
        record = filac.generate(get_pool().connection, case_id, force=force)
    except LookupError:
        raise HTTPException(404, "Case not found") from None
    except LLMError as exc:
        raise HTTPException(502, f"FILAC generation failed: {exc}") from exc
    return FilacOut.model_validate(record)
