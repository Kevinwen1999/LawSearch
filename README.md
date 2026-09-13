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
app/          FastAPI app, settings, DB and embedding helpers
migrations/   numbered SQL migrations, applied by scripts/migrate.py
scripts/      migrate, smoke_test (later: ingestion jobs)
eval/         retrieval eval seed set — AI-drafted, needs human verification
```
