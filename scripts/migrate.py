"""Apply pending SQL migrations in filename order."""

from pathlib import Path

import psycopg

from app.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def main() -> None:
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        conn.commit()

        pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.stem not in applied)
        if not pending:
            print("no pending migrations")
            return

        for path in pending:
            print(f"applying {path.name}")
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            conn.commit()

        print(f"applied {len(pending)} migration(s)")


if __name__ == "__main__":
    main()
