# LawSearch

Scenario → relevant Canadian authorities (Ontario + federal) → FILAC summaries.
See [implementation-plan.md](implementation-plan.md) for the design and
[stack-and-setup.md](stack-and-setup.md) for stack decisions.

## Architecture

### Components

```mermaid
flowchart LR
    user(["Researcher"]) --> ui["Streamlit UI<br/>ui/streamlit_app.py :8501"]
    tunnel["Cloudflare Tunnel<br/>(optional, for remote testing)"] -.-> ui
    ui -- HTTP --> api["FastAPI<br/>app/main.py :8000"]

    subgraph gpu["Local GPU models"]
        emb["bge-m3<br/>embeddings"]
        rr["bge-reranker-v2-m3<br/>cross-encoder"]
    end

    subgraph llm["LLMs (app/llm.py)"]
        local["LM Studio<br/>qwen3.8-27b"]
        claude["Claude<br/>(claude-cli or API)"]
    end

    api --> emb & rr
    api -- "fingerprint<br/>(falls back to Claude)" --> local
    api -- "FILAC briefs,<br/>fingerprint fallback" --> claude
    api --> pg[("Postgres 17<br/>pgvector HNSW + pg_textsearch BM25")]
    api --> minio[("MinIO<br/>uploaded files")]
```

### Scenario search (`POST /scenarios`)

```mermaid
flowchart TD
    input["Scenario text or file<br/>(PDF / DOCX / TXT)"] --> intake["Intake: extract text, OCR fallback<br/>app/intake.py → file stored in MinIO"]
    intake --> fp["Fingerprint (LLM, structured JSON)<br/>jurisdiction · issues · key facts ·<br/>candidate statutes · search terms<br/>app/fingerprint.py"]
    fp --> gate{"Jurisdiction gate"}
    gate -- "other province" --> unsupported["Warn: not covered yet<br/>(no search)"]
    gate -- "jurisdiction unstated<br/>and it matters" --> clarify["Ask a clarifying question<br/>(no search)"]
    gate -- ok --> scope["Scope the search<br/>Ontario → cases from ONCA + SCC<br/>legislation → scenario's jurisdiction<br/>+ Constitution + named statutes"]

    scope --> combined["Combined query<br/>all fingerprint fields"]
    scope --> issues["One query per issue<br/>(up to 8)"]

    subgraph search["retrieval.search() — run for each query"]
        direction TB
        cand["BM25 + vector over case chunks<br/>→ RRF fusion"] --> expand["Citation-graph expansion<br/>(cases cited by top results)"]
        expand --> rerank["Cross-encoder rerank<br/>+ court and citation-count priors"]
        rerank --> sections["Statute sections: BM25 + vector<br/>+ sections cited by top cases"]
    end

    combined --> search
    issues --> search
    search --> merge["Merge (retrieval.merge_issue_results)<br/>combined query's top 3 cases first, then round-robin across issues;<br/>issue results must pass the reranker, and issue sections<br/>must come from a law the scenario already supports"]
    merge --> results["Ranked cases + relevant legislation"]
    results --> brief["FILAC brief on demand<br/>POST /cases/{id}/filac → Claude<br/>every item anchored to a paragraph and verified"]
```

Typed queries (`POST /search`) skip intake, fingerprinting and the per-issue merge: one
`retrieval.search()` call over the whole corpus. A decision that isn't in the corpus (e.g. an
Ontario Superior Court ruling) can be uploaded to `POST /uploads/decisions` for a FILAC brief
plus related authorities; it is never added to the searchable corpus.

### Data pipeline (offline)

```mermaid
flowchart LR
    subgraph sources["Sources"]
        a2aj_cases["A2AJ canadian-case-law<br/>federal courts + tribunals, ONCA"]
        jl["Justice Laws XML<br/>+ Constitution Acts"]
        a2aj_laws["A2AJ canadian-laws<br/>Ontario Acts (no regulations)"]
    end

    a2aj_cases --> ingest_cases["ingest_a2aj<br/>chunk by paragraph, embed"] --> cases[("cases<br/>case_chunks")]
    jl --> ingest_fed["ingest_legislation"] --> leg[("legislation<br/>legislation_sections")]
    a2aj_laws --> ingest_on["ingest_ontario_legislation"] --> leg

    cases --> cite["load_citations<br/>case → case graph,<br/>cited_by_count"] --> edges[("citation_edges")]
    cases & leg --> link["link_statutes<br/>case → statute section references"] --> edges
    edges --> reverify["filac_cli --reverify-all<br/>re-link cached briefs"]
```

`rebuild.ps1` runs the federal steps in order. ONCA
(`python -m scripts.ingest_a2aj ONCA`) and Ontario legislation
(`python -m scripts.ingest_ontario_legislation`) are separate commands. Re-run
`load_citations` and `link_statutes` after either.

## Prerequisites

- Docker Desktop (WSL2 backend; virtualization enabled in BIOS)
- Python 3.12 (python.org build, not the Microsoft Store one)
- NVIDIA GPU recommended for embeddings

## Setup

```powershell
copy .env.example .env
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# sentence-transformers pulls CPU-only torch from PyPI; replace it with the CUDA build.
# Match the version pip installed (check with: pip show torch).
.venv\Scripts\python -m pip install --force-reinstall --no-deps torch==<version> --index-url https://download.pytorch.org/whl/cu130

docker compose up -d
.venv\Scripts\python -m scripts.migrate
.venv\Scripts\python -m scripts.smoke_test
```

The first smoke test run downloads `BAAI/bge-m3` (~4.3 GB) into `HF_HOME` from `.env`
(A2AJ dataset downloads land there too — point it at a drive with room).

## Run it

With the data loaded, one command starts the database, the API (waits for the models to
load) and the UI at http://localhost:8501. Ctrl+C stops the UI and API. The database keeps
running until `docker compose stop`. API output goes to `logs\api.log`.

```powershell
.\run.ps1          # database + API + UI
.\run.ps1 -NoUi    # database + API only
```

To build or refresh the data, `rebuild.ps1` runs the load steps below in order. It asks
before starting; `-Yes` skips the prompt. Every step is safe to re-run, and a failed step
prints the command to resume from it.

```powershell
.\rebuild.ps1                                    # migrate, cases (~5 h), citations, legislation, statute-links, briefs
.\rebuild.ps1 -From legislation                  # after pulling a newer Justice Laws consolidation
.\rebuild.ps1 -From citations -To citations      # a single step
```

If Windows blocks the scripts, run them as `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

## Load the case corpus (Phase 1)

Federal courts and tribunals from A2AJ: ~117k decisions → 3.43M chunks, ~25 GB in
Postgres (including the 8.9 GB HNSW and 0.8 GB BM25 indexes). Needs ~3 GB more for
parquet downloads.

```powershell
# Preview chunk counts and storage without touching the GPU or DB
.venv\Scripts\python -m scripts.ingest_a2aj --federal --dry-run

# Load everything on one GPU (~5 h on an RTX 3090), then build the HNSW + BM25 indexes
.venv\Scripts\python -m scripts.ingest_a2aj --federal
```

Re-runs skip decisions already loaded, so an interrupted load resumes. With two GPUs,
split courts across processes and build the index once:

```powershell
$env:EMBEDDING_DEVICE="cuda:0"; .venv\Scripts\python -m scripts.ingest_a2aj SCC FC FPSLREB --no-index
$env:EMBEDDING_DEVICE="cuda:1"; .venv\Scripts\python -m scripts.ingest_a2aj RAD SST FCA --no-index
.venv\Scripts\python -m scripts.ingest_a2aj --build-index
```

Coverage notes: FC and FCA decisions start in 2001; ONSC is not in A2AJ at all.

## Search (Phase 2)

Hybrid retrieval: BM25 (pg_textsearch) and vector (HNSW) search over chunks, each
collapsed to case ranks and fused with Reciprocal Rank Fusion. Each case returns its
best passages with paragraph anchors. `--mode lexical|vector` runs one retriever alone.

```powershell
.venv\Scripts\python -m scripts.search_cli "side business losses treated as a hobby" -k 5
.venv\Scripts\python -m scripts.search_cli "right to counsel breath sample" --mode lexical --court SCC
```

API (`uvicorn app.main:app`, see below):

```http
POST /search
{"query": "reasonable expectation of profit hobby farm losses", "k": 10,
 "mode": "hybrid", "courts": ["TCC", "FCA"], "date_from": "2005-01-01"}
```

The Phase 2 baseline on the original 14 SCC-gold scenarios is in
`eval/runs/phase2-baseline.json` (hybrid recall@10 0.607). Phase 4 below supersedes it.

## Retrieval quality (Phase 4)

The default search now adds three stages on top of hybrid fusion:

1. **Citation-graph expansion** — cases cited by several of the top 20 fused results join
   the candidate pool as a third RRF list, so a foundational authority (Vavilov, Baker)
   surfaces even when the lower-court decisions applying it outrank it textually.
2. **Authority priors** — small additive boosts by court level and in-corpus citation count.
3. **Cross-encoder rerank** — `BAAI/bge-reranker-v2-m3` scores the best passages of the top
   40 cases; its rank is blended with the pre-rerank rank.

```powershell
# rebuild the citation graph after any corpus load (~30 s)
.venv\Scripts\python -m scripts.load_citations

# compare pipelines on the tune/test splits, or re-tune weights on tune only
.venv\Scripts\python -m scripts.eval_retrieval -v --out eval/runs/<label>.json
.venv\Scripts\python -m scripts.eval_retrieval --tune
```

The graph holds 535,778 case→case edges: A2AJ `cases_cited` lists (French court codes
mapped to English) plus Supreme Court Reports citations extracted from decision text, which
A2AJ's neutral-citation lists omit (Baker: 3,207 citing decisions, previously invisible).

**Eval set.** 33 scored scenarios, split `tune` (17) / `test` (16): the 15 hand-written
scenarios whose gold answers are SCC cases, plus 18 drafted from real FC, FCA, TCC, RAD,
SST, FPSLREB, CHRT, CIRB and CITT decisions (`scripts/draft_eval_scenarios.py`). Weights
were chosen on `tune` only, requiring neither group to fall below phase 2 and preferring
the smallest weights among near-ties. None of the gold answers are human-verified yet.

Held-out **test** split (`eval/runs/phase4.json`):

| Pipeline | Recall@10 | Recall@50 | MRR | nDCG@10 |
|---|---|---|---|---|
| Hybrid (phase 2) | 0.552 | 0.615 | 0.587 | 0.498 |
| + graph only | 0.661 | 0.802 | 0.555 | 0.533 |
| + priors only | 0.552 | 0.677 | 0.643 | 0.535 |
| + rerank only | 0.552 | 0.615 | 0.565 | 0.490 |
| **Phase 4 default** | **0.630** | **0.823** | 0.565 | **0.532** |

All 33 scenarios by group, phase 2 → phase 4: SCC-gold nDCG@10 0.361 → 0.537;
lower-court nDCG@10 0.424 → 0.462 (recall@10 0.417 → 0.532), so boosting authorities did
not bury lower-court answers. Warm latency: p50 1.1 s, p90 1.4 s (rerank ~0.6 s, BM25 on
long scenarios ~0.35 s).

Limits: 16 test scenarios is small (one authority moves recall by several points); MRR is
flat, so the gain is mostly recall and ranking depth rather than the top result; drafted
scenarios are known-item style, so an equally relevant sibling decision counts as a miss;
and there is no subsequent-treatment signal (A2AJ has no followed/overruled data), so a
superseded authority like Dunsmuir can still outrank Vavilov.

## FILAC briefs and UI (Phase 3)

Each case can get a FILAC brief (Facts, Issues, Law, Analysis, Conclusion) where every item
cites the paragraph it comes from, or a passage number for decisions without paragraph
numbers. Briefs are checked against the decision text (anchors exist, cited authorities
actually appear; case citations are resolved against the corpus) and cached per case,
prompt version and model, so each case is generated once.

```powershell
# terminal 1: API (loads the embedding model once)
.venv\Scripts\python -m uvicorn app.main:app
# terminal 2: UI at http://localhost:8501
.venv\Scripts\python -m streamlit run ui/streamlit_app.py

# or one brief from the CLI
.venv\Scripts\python -m scripts.filac_cli "2008 SCC 27"
```

Backends (`FILAC_BACKEND` in `.env`), same prompt, schema, verification and cache:

| Backend | Uses | For |
|---|---|---|
| `claude-cli` (default) | `claude -p` on the Claude Code CLI's own login | Local testing on a subscription; ~7k tokens of CLI overhead per call and subject to plan usage limits |
| `api` | Anthropic SDK with `ANTHROPIC_API_KEY`; server-side refusal fallback enabled | Anyone else using the app. Re-check brief quality after switching |

Measured on `claude-cli` with Claude Opus 5: 19k-char decision 36 s, 86k-char unnumbered
decision 67 s; 0 verification problems on the three briefs generated so far.

API: `GET /cases/{case_id}/filac` returns a cached brief (404 if none),
`POST /cases/{case_id}/filac?force=false` generates one; `GET /courts` lists courts.

## Federal legislation (Phase 5)

Search now returns the statute sections behind a scenario, next to the cases. Legislation
comes from the official Justice Laws XML (4,016 acts and regulations) plus the Constitution
Acts, 1867 and 1982, including the Charter. That is 141,979 section chunks, embedded and
BM25-indexed like case chunks. Only the current consolidation is loaded; each section keeps
its in-force start date.

```powershell
# clone + parse Justice Laws XML and the Constitution page, embed, index (clears statute links)
.venv\Scripts\python -m scripts.ingest_legislation --dry-run
.venv\Scripts\python -m scripts.ingest_legislation
# extract case->statute references from every decision (~13 min); --sample N prints link context
.venv\Scripts\python -m scripts.link_statutes --sample 20
# re-run FILAC verification on cached briefs so Law items link to sections (no model calls)
.venv\Scripts\python -m scripts.filac_cli --reverify-all
```

**Case→statute links.** `app/statute_refs.py` finds references like "s. 97(1)(b) of the
Immigration and Refugee Protection Act", "IRPA, s. 170(i)", "subsection 21(3) of the Act"
(resolved to the act the decision defined as "the Act") and statute citations, and links them
to the section chunk covering the pinpoint. It found 292,010 links, in 77% of decisions. Each
section stores how many decisions cite it.

**Section ranking.** BM25 and vector hits over section chunks are grouped to one result per
section, then fused with RRF. A third list adds sections cited by at least 2 of the top 20
ranked cases, so a query about an avoidance scheme surfaces ITA s. 245 because the GAAR cases
cite it.

**FILAC.** Statute items in a brief's Law section link to the resolved sections. A link is
flagged when the section's current wording came into force after the decision, since the
court applied an earlier version.

**UI and API.** The UI has a "Relevant legislation" panel with "Decisions citing this
section", and a "Legislation cited" list per case.

- `POST /search` returns `sections` alongside `results`.
- `GET /sections/{chunk_id}?limit=` returns a section with the decisions citing it.
- `GET /cases/{case_id}/statutes` lists the sections a decision cites.

**Eval.** 20 scenarios carry statute gold (37 sections). The SCC scenarios' gold is
hand-written; for drafted scenarios it is the sections their source decision cites 2+ times.
Held-out **test** split (`eval/runs/phase5.json`):

| Pipeline | Recall@5 | Recall@10 | MRR |
|---|---|---|---|
| Section text only (BM25 + vector) | 0.394 | 0.455 | 0.346 |
| + sections cited by top cases | 0.530 | 0.682 | 0.530 |
| **Phase 5 default** (+ citation-count prior) | **0.621** | **0.712** | **0.594** |

Tune split, text only → default: recall@10 0.370 → 0.778, MRR 0.258 → 0.627.

Case ranking is unchanged from Phase 4.

Limits:
- **Current text only.** An old decision citing a since-renumbered section links to today's
  section with that number. A reference to a repealed predecessor act links to its
  replacement.
- **"The Act" can resolve to the wrong act.** It resolves to the act the decision last
  defined or named in full, which can be wrong when a decision discusses several.
- **Charter provisions rank weakly.** The Charter's sections are short and cited in general
  terms, so on the criminal scenarios ss. 8, 9 and 11 rank 11th, 15th and 8th.
- **Extracted gold is unreviewed.** It reflects what the source decision cites, which is not
  always what the scenario needs.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

## Run the API

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload
```

`GET /health` reports extension versions and corpus counts; `POST /search` runs the
retriever; interactive docs at `http://localhost:8000/docs`.

## Services

| Service | Port | Notes |
|---|---|---|
| Postgres 17 + pgvector + pg_textsearch | 5432 | custom image in `docker/postgres`; `docker exec -it lawsearch-db psql -U lawsearch` |
| MinIO API / console | 9000 / 9001 | default creds `minioadmin` / `minioadmin` |

## Layout

```
app/          FastAPI app, settings, DB pool, embeddings, reranker, chunking, citations,
              statute parsing + reference extraction, section search,
              retrieval (gather + rank), FILAC extraction + verification, LLM backends
docker/       custom Postgres image (pgvector + pg_textsearch)
migrations/   numbered SQL migrations, applied by scripts/migrate.py
run.ps1       start database + API + UI
rebuild.ps1   load or refresh all data, steps in dependency order
scripts/      common.ps1 (shared by run/rebuild), migrate, smoke_test, ingest_a2aj,
              load_citations, search_cli, eval_retrieval, draft_eval_scenarios, filac_cli,
              ingest_legislation, link_statutes
ui/           Streamlit MVP (talks to the API over HTTP)
tests/        pytest unit tests
eval/         eval scenarios (citations resolved, relevance needs human review) and runs/
```
