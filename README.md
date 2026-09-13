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

Federal courts and tribunals from A2AJ: ~117k decisions → 3.43M chunks, ~16 GB in
Postgres. Needs ~3 GB for parquet downloads plus the database.

```powershell
# Preview chunk counts and storage without touching the GPU or DB
.venv\Scripts\python -m scripts.ingest_a2aj --federal --dry-run

# Load everything on one GPU (~5 h on an RTX 3090), then build the HNSW index
.venv\Scripts\python -m scripts.ingest_a2aj --federal
```

Re-runs skip decisions already loaded, so an interrupted load resumes. With two GPUs,
split courts across processes and build the index once:

```powershell
$env:EMBEDDING_DEVICE="cuda:0"; .venv\Scripts\python -m scripts.ingest_a2aj SCC FC FPSLREB --no-index
$env:EMBEDDING_DEVICE="cuda:1"; .venv\Scripts\python -m scripts.ingest_a2aj RAD SST FCA --no-index
.venv\Scripts\python -m scripts.ingest_a2aj --build-index
```

Query from the CLI (keyword search wants terms, not sentences; vector search takes prose):

```powershell
.venv\Scripts\python -m scripts.search_cli "right to counsel breath sample" --mode lexical
.venv\Scripts\python -m scripts.search_cli "police did not let him call a lawyer" --court SCC -k 5
```

Coverage notes: FC and FCA decisions start in 2001; ONSC is not in A2AJ at all.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

## Run the API

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload
```

`GET http://localhost:8000/health` reports pgvector version and corpus counts.

## Services

| Service | Port | Notes |
|---|---|---|
| Postgres 16 + pgvector | 5432 | `docker exec -it lawsearch-db psql -U lawsearch` |
| MinIO API / console | 9000 / 9001 | default creds `minioadmin` / `minioadmin` |

## Layout

```
app/          FastAPI app, settings, DB, embedding and chunking helpers
migrations/   numbered SQL migrations, applied by scripts/migrate.py
scripts/      migrate, smoke_test, ingest_a2aj (corpus loader), search_cli
tests/        pytest unit tests
eval/         retrieval eval seed set — citations resolved, relevance needs human review
```
