from fastapi import FastAPI

from app.db import connect

app = FastAPI(title="LawSearch", version="0.1.0")


@app.get("/health")
def health() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()
        cur.execute("SELECT count(*) FROM cases")
        case_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM case_chunks WHERE embedding IS NOT NULL")
        embedded_chunks = cur.fetchone()[0]

    return {
        "status": "ok",
        "pgvector": row[0] if row else None,
        "cases": case_count,
        "embedded_chunks": embedded_chunks,
    }
