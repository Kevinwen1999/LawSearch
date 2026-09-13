# LawSearch

Scenario → relevant Canadian authorities (Ontario + federal) → FILAC summaries.
See [implementation-plan.md](implementation-plan.md) for the design and
[stack-and-setup.md](stack-and-setup.md) for stack decisions.

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

Score retrieval against the eval set (Recall@10/50, MRR, nDCG@10 per mode):

```powershell
.venv\Scripts\python -m scripts.eval_retrieval -v --out eval/runs/<label>.json
```

| Run | Mode | Recall@10 | Recall@50 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| phase2-baseline | lexical | 0.464 | 0.679 | 0.323 | 0.318 |
| phase2-baseline | vector | 0.429 | 0.714 | 0.269 | 0.283 |
| phase2-baseline | hybrid | 0.607 | 0.714 | 0.357 | 0.387 |

Known weakness: foundational SCC authorities (Vavilov, Baker, Kanthasamy, Moore) lose
to the many FC decisions that apply them — the target of Phase 4's citation-graph
expansion.

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
app/          FastAPI app, settings, DB pool, embeddings, chunking, hybrid retrieval,
              FILAC extraction + verification, LLM backends
docker/       custom Postgres image (pgvector + pg_textsearch)
migrations/   numbered SQL migrations, applied by scripts/migrate.py
scripts/      migrate, smoke_test, ingest_a2aj, search_cli, eval_retrieval, filac_cli
ui/           Streamlit MVP (talks to the API over HTTP)
tests/        pytest unit tests
eval/         eval scenarios (citations resolved, relevance needs human review) and runs/
```
