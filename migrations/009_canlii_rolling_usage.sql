-- CanLII's daily cap (5,000) resets on a day boundary CanLII doesn't document. Count usage in
-- hourly buckets and cap any rolling 24 hours instead: staying under the cap in every 24-hour
-- window keeps it under on every calendar day, wherever that day starts.
CREATE TABLE canlii_usage_hourly (
    hour            timestamptz PRIMARY KEY,   -- date_trunc('hour', request time)
    queries         integer NOT NULL DEFAULT 0,
    last_request_at timestamptz
);

INSERT INTO canlii_usage_hourly (hour, queries, last_request_at)
SELECT coalesce(date_trunc('hour', last_request_at), day::timestamptz), queries, last_request_at
FROM canlii_usage;

DROP TABLE canlii_usage;
