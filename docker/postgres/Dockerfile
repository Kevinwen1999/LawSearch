# Postgres 17 + pgvector + pg_textsearch (BM25 ranked keyword search, PostgreSQL license).
# pg_textsearch supports only PG 17/18, which is why this image left pg16.
FROM pgvector/pgvector:0.8.6-pg17-bookworm

ARG PG_TEXTSEARCH_VERSION=1.4.0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl unzip; \
    curl -fsSL -o /tmp/pg_textsearch.zip \
      "https://github.com/timescale/pg_textsearch/releases/download/v${PG_TEXTSEARCH_VERSION}/pg-textsearch-v${PG_TEXTSEARCH_VERSION}-pg17-amd64.zip"; \
    unzip /tmp/pg_textsearch.zip -d /tmp/pg_textsearch; \
    apt-get install -y --no-install-recommends /tmp/pg_textsearch/*.deb; \
    apt-get purge -y --auto-remove curl unzip; \
    rm -rf /tmp/pg_textsearch /tmp/pg_textsearch.zip /var/lib/apt/lists/*
