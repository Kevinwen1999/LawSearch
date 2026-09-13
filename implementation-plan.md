# Legal Research Tool — Implementation Plan

**Goal:** Given a plain-language scenario (typed or uploaded as a file), find relevant Canadian
authorities (cases *and* legislation) for **Ontario + federal** law, and summarize each case by
**FILAC** (Facts, Issues, Law, Analysis, Conclusion).

**Design in one line:** A2AJ open full-text corpus as the search/retrieval/FILAC engine, Justice
Laws XML + e-Laws as the legislation corpus, CanLII metadata API as a breadth/detection layer for
the gaps (notably ONSC), and a user-upload path as the bridge when full text isn't in the corpus.

---

## 1. Architecture at a glance

```
                 ┌─────────────── Offline ingestion (batch jobs) ───────────────┐
                 │  A2AJ cases (Parquet/HF)   Justice Laws XML   e-Laws fetch     │
                 │        │                        │                 │            │
                 │   parse + chunk           parse hierarchy    parse hierarchy   │
                 │        │                        │                 │            │
                 │   embed + index ──────►  Postgres + pgvector  ◄──── embed      │
                 │        │                  (cases, sections,        │           │
                 │   citation extraction ──► citation_edges) ◄────────┘           │
                 └────────────────────────────────┬──────────────────────────────┘
                                                   │
   User input ──► Intake ──► Scenario ──► Hybrid retrieval ──► Graph ──► Rerank ──► FILAC ──► Present
  (text / file)   (parse)   fingerprint   (lexical+vector)    expansion            extract   (+ cross-links,
                                          cases + sections                                    verify links)
                                                   │
                                          CanLII metadata API (detect + link-out for ONSC/gap cases)
```

**Components:** ingestion workers, a Postgres+pgvector store, a retrieval service, an LLM service
(fingerprinting + FILAC + rerank), a CanLII client, a FastAPI backend, and a thin frontend.

---

## 2. Tech stack (concrete defaults)

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Legal/NLP ecosystem is Python |
| API | FastAPI + uvicorn | async, easy typed endpoints |
| DB / search | Postgres 16 + `pgvector` + `tsvector` | one store for lexical **and** vector to start; move to OpenSearch only if scale demands |
| Jobs | Plain scripts + a `jobs` table for ingestion; RQ/Celery + Redis if you want a queue | ingestion is batch, not real-time |
| Corpus IO | HuggingFace `datasets`, `pyarrow`/`polars` (A2AJ Parquet); `lxml` (Justice Laws XML) | |
| Embeddings | A strong retrieval model (hosted embedding API or `sentence-transformers`); evaluate a legal-domain model if recall lags | chunk-level |
| Reranker | Cross-encoder (e.g. a `bge-reranker`) or LLM-as-reranker on top-K | |
| LLM | Hosted model for fingerprinting + FILAC, **structured/JSON output** | |
| File parsing | `pymupdf`/`pdfplumber` (PDF), `python-docx` (DOCX); `ocrmypdf`/tesseract fallback for scans | reuse the `file-reading` router logic |
| Citation parsing | `legal-citation-parser` (CanLII UID resolution) + custom regex for statute refs | |
| Frontend | Streamlit for the internal MVP → Next.js/React for the real UI | ship value before polishing UI |
| Local infra | Docker Compose (Postgres, Redis, app); MinIO/S3 for raw corpus + uploads | |

Keep infra minimal until a bottleneck proves otherwise: **Postgres-only search is enough for a
191k-case corpus.**

---

## 3. Core data model

```sql
-- Cases (full text, from A2AJ or user upload)
cases(
  id                uuid pk,
  neutral_citation  text,           -- e.g. '2023 SCC 17'  (nullable for uploads)
  style_of_cause    text,
  court             text,           -- 'SCC','FCA','FC','TCC','ONCA', tribunals...
  jurisdiction      text,           -- 'federal' | 'ontario'
  decision_date     date,
  language          text,           -- 'en' | 'fr'
  source            text,           -- 'a2aj' | 'upload' | ...
  url_official      text,           -- authoritative copy (verify link)
  url_canlii        text,
  full_text         text,
  ingested_at       timestamptz
)

case_chunks(
  id          uuid pk,
  case_id     uuid fk -> cases,
  para_no     int,                  -- from the decision's own numbering when present
  text        text,
  embedding   vector(N)
)

-- Legislation, section-grained
legislation(
  id                uuid pk,
  citation          text,           -- 'RSC 1985, c C-46', 'RSO 1990, c ...'
  title             text,
  jurisdiction      text,           -- 'federal' | 'ontario'
  consolidation_date date,
  language          text
)

legislation_sections(
  id             uuid pk,
  legislation_id uuid fk -> legislation,
  section_label  text,              -- '24', '24(2)'
  hierarchy_path text,              -- 'Part I > s.24 > (2)'
  text           text,
  in_force_start date,              -- point-in-time
  in_force_end   date,             -- null = current
  embedding      vector(N)
)

-- Unified citation graph (case->case, case->statute)
citation_edges(
  id           uuid pk,
  src_type     text,                -- 'case'
  src_id       uuid,
  dst_type     text,                -- 'case' | 'legislation_section'
  dst_id       uuid,                -- null if unresolved
  citation_raw text,                -- as found in text
  edge_kind    text,                -- 'case_cites_case' | 'case_cites_statute'
  confidence   real
)
```

Add GIN index on a `tsvector` column of `case_chunks.text` / `legislation_sections.text`, and an
IVFFlat/HNSW index on the `embedding` columns.

---

## 4. Phased build plan

Each phase has a **definition of done (DoD)**. Sizes are relative (S/M/L), not calendar promises.
Phases 1–3 get you a working federal vertical slice fast; deepen after.

### Phase 0 — Foundations (S)
- Repo, Docker Compose (Postgres+pgvector, Redis, MinIO), FastAPI skeleton, migrations.
- Create the schema above; wire embeddings + a smoke-test vector query.
- **Stand up the eval seed set now** (§6) — 15–30 scenarios with hand-picked relevant authorities.
- **DoD:** `docker compose up` gives a DB with the schema and one embedded test doc you can query.

### Phase 1 — Federal case corpus (M)
- Ingestion worker: pull A2AJ case Parquet/HF, filter to federal courts/tribunals, normalize into
  `cases`, chunk into `case_chunks` by paragraph, embed, index.
- Populate `case_chunks.tsvector`.
- **DoD:** federal corpus loaded; both a lexical and a vector query return sane results from the CLI.

### Phase 2 — Retrieval v1 + API (M)
- Hybrid retriever: run BM25 (`tsvector`) and vector search, fuse with **Reciprocal Rank Fusion**,
  return top-K cases.
- `POST /search` accepting raw query text.
- **DoD:** a keyword-ish query returns a ranked case list via the API.

### Phase 3 — FILAC + first vertical slice (M)
- FILAC extractor (§5.3): full-text case → structured Facts/Issues/Law/Analysis/Conclusion with
  paragraph anchors.
- Minimal Streamlit UI: scenario box → ranked cases → expandable FILAC + verify link.
- **DoD:** paste a scenario, get relevant *federal* cases each with a grounded FILAC summary. This is
  the demoable MVP.

### Phase 4 — Retrieval quality (L)
- Graph expansion: for top hits, pull `cases_cited`/`cases_citing` (A2AJ fields), add to candidate pool.
- Cross-encoder rerank of the fused+expanded set against the scenario's *issues*.
- Signal weighting: court level, recency, subsequent treatment.
- Tune against the eval set; track retrieval metrics (§6).
- **DoD:** measurable recall/precision improvement over Phase 2 on the eval set.

### Phase 5 — Legislation corpus + cross-linking (L)
- Federal legislation ingest: parse Justice Laws bulk XML (`lxml`) into `legislation` +
  `legislation_sections` (section-grained), capture consolidation/PIT dates, embed.
- Citation-extraction pass over case text → `citation_edges` (case→statute), normalize to canonical
  citations, resolve to section IDs where possible.
- Retrieval returns **two typed result sets** (cases + sections); fingerprinting queries both.
- Wire statute→interpreting-cases and case→cited-statutes views; feed these into FILAC's **Law** field.
- **DoD:** a scenario returns relevant statute sections alongside cases, and each case's FILAC "Law"
  cites resolved sections.

### Phase 6 — Intake robustness (M)
- File upload: PDF/DOCX/text extraction + OCR fallback; store in MinIO; parse to text.
- Scenario fingerprinting (§5.1): LLM → structured legal fingerprint (jurisdiction, area, issues,
  facts, doctrines, search terms) that drives retrieval.
- **Jurisdiction gate:** if province/level is unstated and matters, ask before retrieving.
- **DoD:** upload a scenario PDF → get the same quality result as typed input.

### Phase 7 — Ontario + gap handling (L)
- Add A2AJ ONCA to the case corpus (mind the pre-1990 gap).
- Ontario legislation: bespoke e-Laws fetcher/parser into the legislation tables; **confirm reuse
  terms** (Queen's Printer for Ontario / OGL–Ontario) before caching.
- CanLII client (§5.4): rate-limited, cached; use it to **detect** relevant ONSC/tribunal cases via
  metadata + citator and surface them as "relevant — view on CanLII" (no text, no FILAC).
- Upload-driven FILAC as the bridge: user supplies an ONSC decision → full FILAC + related-authority
  search on it.
- **DoD:** an Ontario scenario returns ONCA cases + Ontario statutes with FILAC, plus flagged ONSC
  candidates via link-out, and an uploaded ONSC PDF gets full treatment.

### Phase 8 — Hardening (M, ongoing)
- Hallucination guards + citation verification (§5.5), point-in-time correctness on statutes,
  observability/logging, cost controls, and the production React UI.
- **DoD:** every asserted authority resolves to a real corpus doc; no unverifiable citations reach the user.

---

## 5. Tricky parts, spelled out

### 5.1 Scenario fingerprinting
LLM turns prose into a structured object that retrieval consumes:
```json
{
  "jurisdiction": "ontario|federal|unknown",
  "areas_of_law": ["negligence", "occupiers' liability"],
  "issues": ["duty of care of a commercial occupier to an invitee", ...],
  "key_facts": ["slip on unmarked wet floor in a grocery store", ...],
  "causes_of_action": ["negligence"],
  "candidate_statutes": ["Occupiers' Liability Act (Ontario)"],
  "search_terms": ["occupier duty", "invitee", "wet floor", ...],
  "needs_clarification": ["province not stated"]
}
```
`search_terms`/`candidate_statutes` feed lexical search; the whole object feeds semantic search and
rerank. **Never let this step assert a case exists** — it proposes concepts and terms, not holdings.

### 5.2 Chunking
Split by the decision's own paragraph numbering where present (Canadian judgments are usually
numbered); else fixed-size windows with overlap. Store `para_no` so FILAC can anchor claims to
paragraphs. Legislation chunks = one section/subsection each.

### 5.3 FILAC extraction (structured, grounded)
Prompt for strict JSON, one field per FILAC element, **every claim anchored to a paragraph** and a
`"not_stated_in_text"` fallback so the model can decline rather than invent:
```json
{
  "facts": [{"text": "...", "para": 12}],
  "issues": [{"text": "...", "para": 3}],
  "law": [{"authority": "Occupiers' Liability Act, s.3", "kind": "statute", "para": 20},
          {"authority": "2001 SCC 79", "kind": "case", "para": 22}],
  "analysis": [{"text": "...", "para": 25}],
  "conclusion": [{"text": "...", "para": 40}]
}
```
For long judgments: extract per-section, then reconcile. Explicitly instruct the model to attribute
statements to *who said them* (court vs. losing party's argument) to avoid presenting a rejected
argument as the holding.

### 5.4 Rate-limited CanLII client
Token-bucket limiter enforcing **2 req/s, 1 concurrent, ≤5000/day**; aggressive multi-day caching of
metadata + citator responses (they rarely change); cap citator hop-depth. Used only for
detection/link-out, never to fetch or cache full document text.

### 5.5 Citation extraction + verification
- Detect neutral citations (`\d{4}\s[A-Z]+\s\d+`) and statute refs (`R?S[OC]\s\d{4},?\s*c\.?\s*\S+`,
  `s\.?\s*\d+(\(\d+\))?`) in case text; normalize; resolve to IDs.
- Verification gate before display: any authority in a FILAC "Law" field or a relevance rationale must
  resolve to a real row (case or section). Unresolved → mark "unverified," don't present as fact.

### 5.6 Point-in-time
Store `in_force_start/end` per section. When a case relies on a statute, prefer the version in force at
the decision date; when advising on the scenario, use the current consolidation. Surface the version
date in the UI.

---

## 6. Evaluation (build in Phase 0, use throughout)

Two things to measure separately:

**Retrieval quality** — a gold set of scenarios each mapped to a set of known-relevant authorities.
Track Recall@k, Precision@k, MRR, and nDCG as you add graph expansion and rerank. This is how you
know Phase 4/5 actually helped.

**Extraction quality** — a handful of cases with human-written FILAC summaries; score each element for
faithfulness (is every claim supported by the cited paragraph?) and completeness. Grade with a rubric;
optionally LLM-as-judge for scale, spot-checked by a human.

Freeze the seed set early; grow it as real usage surfaces failure modes.

---

## 7. Risks & guardrails

- **Hallucinated authorities** — mitigated by §5.1 (concepts not cases), §5.3 (grounding), §5.5
  (verification gate). This is the single highest-liability failure.
- **Silent coverage gaps** — ONSC/Ontario-tribunal full text isn't openly available; be explicit in the
  UI about what's covered, and use CanLII detection so a relevant-but-unshowable case is at least flagged.
- **Unofficial text** — A2AJ copies are unofficial; always show a verify-against-official link.
- **Point-in-time errors** — see §5.6; wrong statute version is a serious substantive error.
- **Unauthorized practice / no legal advice** — persistent framing as research assistance, AI-summary
  labeling, no advice.
- **Privacy** — decisions contain sensitive personal data and bulk access raises re-identification risk
  for vulnerable parties; don't build features that aggregate personal info across cases, and consider
  redaction in any public-facing surface.

---

## 8. Suggested first two weeks

1. Phase 0 scaffolding + schema + eval seed set.
2. Phase 1 federal case ingestion (start with just the SCC subset to iterate fast, then widen).
3. Phase 2 hybrid retrieval + `/search`.
4. Phase 3 FILAC + Streamlit slice.

That yields a working "scenario → relevant federal cases → FILAC" demo on open data, which is the
riskiest assumption validated. Everything after is depth (retrieval quality, legislation, Ontario, UI).
