from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from app.config import settings


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.database_url, autocommit=autocommit) as conn:
        register_vector(conn)
        yield conn


@lru_cache(maxsize=1)
def get_pool() -> ConnectionPool:
    from app.retrieval import prepare_session

    def configure(conn: psycopg.Connection) -> None:
        register_vector(conn)
        prepare_session(conn)

    return ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.db_pool_size,
        configure=configure,
        open=False,
    )
