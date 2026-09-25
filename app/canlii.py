"""Rate-limited, cached client for CanLII's metadata API (plan §5.4).

CanLII's usage plan: metadata only (no document text, no text search), 5,000 queries/day,
2 requests/second, 1 request at a time, and no increases. Every process shares one budget and
one request slot through Postgres: a session advisory lock serializes requests, and
`canlii_usage` holds the day's count and the last request time used for pacing.

Responses are cached per request path (metadata for weeks, citator lists for days) — only
metadata, since that is all the API returns.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any, Protocol

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.config import settings

BASE_URL = "https://api.canlii.org/v1"
LOCK_KEY = 0x43414E4C4949  # "CANLII"; one request at a time across every process
THROTTLE_RETRIES = 2
THROTTLE_BACKOFF_SECONDS = 1.5


class CanLIIError(RuntimeError):
    pass


class NotConfigured(CanLIIError):
    pass


class BudgetExhausted(CanLIIError):
    pass


@dataclass(frozen=True)
class Response:
    status: int  # 200, or 404 when CanLII has no such database/case
    body: Any


class Store(Protocol):
    def cached(self, path: str, max_age: timedelta) -> Response | None: ...
    def save(self, path: str, response: Response) -> None: ...
    def request_slot(self) -> AbstractContextManager[Callable[[float, int], None]]: ...
    def used_today(self) -> int: ...


class PostgresStore:
    """Cache and limiter state in Postgres. Uses its own autocommit connections so a request's
    usage count is recorded even when the request then fails."""

    def __init__(self, connect: Callable[[], AbstractContextManager[psycopg.Connection]]):
        self._connect = connect

    def cached(self, path: str, max_age: timedelta) -> Response | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, body FROM canlii_cache WHERE path = %s AND fetched_at > now() - %s",
                (path, max_age),
            ).fetchone()
        return Response(*row) if row else None

    def save(self, path: str, response: Response) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO canlii_cache (path, status, body) VALUES (%s, %s, %s)
                ON CONFLICT (path) DO UPDATE
                SET status = excluded.status, body = excluded.body, fetched_at = now()
                """,
                (path, response.status, Jsonb(response.body)),
            )

    @contextmanager
    def request_slot(self) -> Iterator[Callable[[float, int], None]]:
        """Hold the one CanLII request slot. The yielded `wait_turn(min_interval, daily_limit)`
        must be called before every HTTP attempt: it enforces the budget and pacing, then
        records the attempt."""
        with self._connect() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            try:

                def wait_turn(min_interval: float, daily_limit: int) -> None:
                    used, since_last = conn.execute(
                        """
                        SELECT coalesce((SELECT queries FROM canlii_usage
                                         WHERE day = (now() AT TIME ZONE 'UTC')::date), 0),
                               extract(epoch FROM clock_timestamp() - max(last_request_at))
                        FROM canlii_usage
                        """
                    ).fetchone()
                    if used >= daily_limit:
                        raise BudgetExhausted(
                            f"CanLII daily budget used up ({used}/{daily_limit} queries today, UTC)"
                        )
                    if since_last is not None and since_last < min_interval:
                        time.sleep(min_interval - float(since_last))
                    conn.execute(
                        """
                        INSERT INTO canlii_usage (day, queries, last_request_at)
                        VALUES ((now() AT TIME ZONE 'UTC')::date, 1, clock_timestamp())
                        ON CONFLICT (day) DO UPDATE
                        SET queries = canlii_usage.queries + 1, last_request_at = clock_timestamp()
                        """
                    )

                yield wait_turn
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))

    def used_today(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT queries FROM canlii_usage WHERE day = (now() AT TIME ZONE 'UTC')::date"
            ).fetchone()
        return row[0] if row else 0


class CanLIIClient:
    def __init__(
        self,
        store: Store,
        api_key: str | None = None,
        *,
        http: httpx.Client | None = None,
        min_interval: float | None = None,
        daily_limit: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.store = store
        self.api_key = settings.canlii_api_key if api_key is None else api_key
        self.http = http or httpx.Client(timeout=settings.canlii_timeout_seconds)
        self.min_interval = settings.canlii_min_interval_seconds if min_interval is None else min_interval
        self.daily_limit = settings.canlii_daily_limit if daily_limit is None else daily_limit
        self.sleep = sleep
        self.queries_sent = 0  # by this client instance, for per-request accounting

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def databases(self) -> dict[str, dict]:
        """Case databases by id: {"onsc": {"jurisdiction": "on", "name": "Superior Court of Justice"}}."""
        response = self._get(
            "/caseBrowse/en/",
            timedelta(days=settings.canlii_metadata_ttl_days),
            lambda body: {
                d["databaseId"]: {"jurisdiction": d["jurisdiction"], "name": d["name"]}
                for d in body["caseDatabases"]
            },
        )
        return response.body if response.status == 200 else {}

    def case_metadata(self, database_id: str, case_id: str) -> dict | None:
        response = self._get(
            f"/caseBrowse/en/{database_id}/{case_id}/",
            timedelta(days=settings.canlii_metadata_ttl_days),
        )
        return response.body if response.status == 200 else None

    def citing_cases(self, database_id: str, case_id: str) -> list[dict] | None:
        """Decisions citing a case, as {"databaseId", "caseId", "title", "citation"}. None when
        CanLII doesn't have the case. Landmark cases return tens of thousands (Vavilov: 16k,
        5.7 MB), so only these four fields are cached."""
        response = self._get(
            f"/caseCitator/en/{database_id}/{case_id}/citingCases",
            timedelta(days=settings.canlii_citator_ttl_days),
            lambda body: [
                {
                    "databaseId": c["databaseId"],
                    "caseId": _case_id(c["caseId"]),
                    "title": c.get("title"),
                    "citation": c.get("citation"),
                }
                for c in body.get("citingCases", [])
            ],
        )
        return response.body if response.status == 200 else None

    def _get(self, path: str, max_age: timedelta, transform: Callable[[Any], Any] | None = None) -> Response:
        if hit := self.store.cached(path, max_age):
            return hit
        if not self.configured:
            raise NotConfigured("CANLII_API_KEY is not set")
        with self.store.request_slot() as wait_turn:
            # Another process may have fetched it while this one waited for the slot.
            if hit := self.store.cached(path, max_age):
                return hit
            response = self._fetch(path, wait_turn)
        if response.status == 200 and transform:
            response = Response(200, transform(response.body))
        self.store.save(path, response)
        return response

    def _fetch(self, path: str, wait_turn: Callable[[float, int], None]) -> Response:
        for attempt in range(THROTTLE_RETRIES + 1):
            wait_turn(self.min_interval, self.daily_limit)
            self.queries_sent += 1
            try:
                r = self.http.get(BASE_URL + path, params={"api_key": self.api_key})
            except httpx.HTTPError as exc:
                raise CanLIIError(f"CanLII request failed: {type(exc).__name__}") from None
            if r.status_code == 429 and attempt < THROTTLE_RETRIES:
                self.sleep(THROTTLE_BACKOFF_SECONDS * (attempt + 1))
                continue
            if r.status_code in (200, 404):
                return Response(r.status_code, r.json() if r.status_code == 200 else {})
            # Never echo the URL: it carries the api key.
            raise CanLIIError(f"CanLII returned HTTP {r.status_code} for {path}")
        raise AssertionError("unreachable")


def _case_id(value: Any) -> str:
    # The citator returns {"en": "2024onsc1087"}; case metadata returns a plain string.
    if isinstance(value, dict):
        return value.get("en") or next(iter(value.values()), "")
    return value


@lru_cache(maxsize=1)
def _http() -> httpx.Client:
    return httpx.Client(timeout=settings.canlii_timeout_seconds)


def default_client() -> CanLIIClient:
    """A client on the shared HTTP connection pool. Create one per request: `queries_sent`
    counts that request's spend."""
    from app.db import connect

    return CanLIIClient(PostgresStore(lambda: connect(autocommit=True)), http=_http())
