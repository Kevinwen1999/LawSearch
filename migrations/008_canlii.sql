-- Phase 8: CanLII metadata client. CanLII's usage plan is metadata only, 5,000 queries/day,
-- 2 requests/second, 1 request at a time; these tables let every process share one budget.

-- Cached API responses keyed by request path (no api_key). Metadata and citator lists only:
-- the API never returns document text, and nothing here stores any.
CREATE TABLE canlii_cache (
    path       text PRIMARY KEY,
    status     integer NOT NULL,   -- 200, or 404 for ids CanLII doesn't have
    body       jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

-- Queries sent per UTC day (every attempt counts, throttled or not) and when the last one went
-- out, for pacing across processes.
CREATE TABLE canlii_usage (
    day             date PRIMARY KEY,
    queries         integer NOT NULL DEFAULT 0,
    last_request_at timestamptz
);
