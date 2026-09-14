from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app import filac, fingerprint, intake, retrieval
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


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    code: str
    kind: str
    title: str
    citation: str | None
    consolidation_date: date | None
    section_no: str
    section_label: str
    marginal_note: str | None
    hierarchy_path: str | None
    text: str
    url: str | None
    in_force_start: date | None
    cited_by_count: int
    score: float = 0.0
    citing_cases: int = 0


class SearchResponse(BaseModel):
    query: str
    mode: retrieval.Mode
    results: list[CaseOut]
    sections: list[SectionOut]
    timings_ms: dict[str, float]


class CitingCaseOut(BaseModel):
    case_id: UUID
    citation: str | None
    style_of_cause: str | None
    court: str | None
    decision_date: date | None
    cited_by_count: int


class SectionDetailOut(BaseModel):
    section: SectionOut
    citing_cases: list[CitingCaseOut]
    citing_cases_total: int


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
        sections=[SectionOut.model_validate(s) for s in result.sections],
        timings_ms=result.timings_ms,
    )


_SECTION_COLUMNS = """
    s.id AS chunk_id, l.code, l.kind, l.title, l.citation, l.consolidation_date, s.section_no,
    s.section_label, s.marginal_note, s.hierarchy_path, s.text, s.url_official AS url,
    s.in_force_start, s.cited_by_count
"""


@app.get("/sections/{chunk_id}", response_model=SectionDetailOut)
def get_section(chunk_id: UUID, limit: int = 20) -> SectionDetailOut:
    """A statute section and the decisions that cite it, most-cited decisions first."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        section = cur.execute(
            f"SELECT {_SECTION_COLUMNS} FROM legislation_sections s JOIN legislation l ON l.id = s.legislation_id "
            "WHERE s.id = %s",
            (chunk_id,),
        ).fetchone()
        if section is None:
            raise HTTPException(404, "Section not found")
        citing = cur.execute(
            """
            SELECT DISTINCT ON (c.cited_by_count, c.id) c.id AS case_id, c.citation, c.style_of_cause,
                   c.court, c.decision_date, c.cited_by_count
            FROM legislation_sections target
            JOIN legislation_sections s ON s.legislation_id = target.legislation_id AND s.section_no = target.section_no
            JOIN citation_edges e ON e.dst_id = s.id AND e.edge_kind = 'case_cites_statute'
            JOIN cases c ON c.id = e.src_id
            WHERE target.id = %s
            ORDER BY c.cited_by_count DESC, c.id
            LIMIT %s
            """,
            (chunk_id, min(limit, 100)),
        ).fetchall()
    return SectionDetailOut(
        section=SectionOut(**section),
        citing_cases=[CitingCaseOut(**c) for c in citing],
        citing_cases_total=section["cited_by_count"],
    )


@app.get("/cases/{case_id}/statutes", response_model=list[SectionOut])
def case_statutes(case_id: UUID) -> list[SectionOut]:
    """Federal statute sections cited in a decision, most-cited sections first."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            f"""
            SELECT DISTINCT ON (s.cited_by_count, l.code, s.section_no) {_SECTION_COLUMNS}
            FROM citation_edges e
            JOIN legislation_sections s ON s.id = e.dst_id
            JOIN legislation l ON l.id = s.legislation_id
            WHERE e.src_id = %s AND e.edge_kind = 'case_cites_statute'
            ORDER BY s.cited_by_count DESC, l.code, s.section_no, s.chunk_no
            """,
            (case_id,),
        ).fetchall()
    return [SectionOut(**r) for r in rows]


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


class FingerprintOut(BaseModel):
    jurisdiction: str
    areas_of_law: list[str]
    issues: list[str]
    key_facts: list[str]
    causes_of_action: list[str]
    candidate_statutes: list[str]
    search_terms: list[str]
    needs_clarification: list[str]
    model: str
    backend: str
    usage: dict


class GateOut(BaseModel):
    status: Literal["ok", "needs_clarification", "unsupported_jurisdiction"]
    message: str | None


class ScenarioResponse(BaseModel):
    source: Literal["upload", "text"]
    upload_key: str | None
    extracted_chars: int
    fingerprint: FingerprintOut
    gate: GateOut
    results: SearchResponse | None


UploadKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}\.[A-Za-z0-9]{1,10}$")]


@app.post("/scenarios", response_model=ScenarioResponse)
async def create_scenario(
    file: UploadFile | None = File(None),
    text: Annotated[str | None, Form()] = None,
    k: Annotated[int, Form(ge=1, le=50)] = 10,
    courts: Annotated[list[CourtCode] | None, Form()] = None,
    date_from: Annotated[date | None, Form()] = None,
    date_to: Annotated[date | None, Form()] = None,
) -> ScenarioResponse:
    """Upload a scenario document (PDF/DOCX/text) or pass raw text; fingerprint it, gate on
    jurisdiction, and run the same retrieval a typed query would use."""
    if file is None and not (text and text.strip()):
        raise HTTPException(400, "provide either a file or text")

    upload_key = None
    if file is not None:
        content = await file.read()
        try:
            scenario_text = intake.extract_text(file.filename or "", content)
        except intake.ExtractionError as exc:
            raise HTTPException(422, str(exc)) from exc
        upload_key = intake.store_upload(file.filename or "upload", content).key
        source = "upload"
    else:
        scenario_text = text.strip()
        source = "text"

    try:
        fp = fingerprint.generate(scenario_text)
    except LLMError as exc:
        raise HTTPException(502, f"fingerprinting failed: {exc}") from exc

    gate = fingerprint.check_jurisdiction(fp)

    results = None
    if gate.status == "ok":
        query = fingerprint.search_query(fp)
        with get_pool().connection() as conn:
            result = retrieval.search(
                conn, query, k=k, courts=courts, date_from=date_from, date_to=date_to
            )
        results = SearchResponse(
            query=query, mode="hybrid",
            results=[CaseOut.model_validate(c) for c in result.cases],
            sections=[SectionOut.model_validate(s) for s in result.sections],
            timings_ms=result.timings_ms,
        )

    return ScenarioResponse(
        source=source,
        upload_key=upload_key,
        extracted_chars=len(scenario_text),
        fingerprint=FingerprintOut(**fp.__dict__),
        gate=GateOut(status=gate.status, message=gate.message),
        results=results,
    )


@app.delete("/scenarios/uploads/{key}")
def delete_scenario_upload(key: UploadKey) -> dict:
    full_key = f"uploads/{key}"
    intake.delete_upload(full_key)
    return {"deleted": full_key}
