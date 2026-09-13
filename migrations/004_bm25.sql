-- Phase 2: BM25 keyword search via pg_textsearch replaces tsvector + ts_rank.
-- ts_rank has no IDF and cannot return top-k without scoring every match (a prose query
-- matched 1.7M chunks and took 51 s just to count). pg_textsearch does block-max WAND.
--
-- The BM25 index itself is built by the bulk loader alongside HNSW
-- (scripts/ingest_a2aj.py --build-index), not here.

CREATE EXTENSION IF NOT EXISTS pg_textsearch;

DROP INDEX IF EXISTS case_chunks_tsv_idx;
ALTER TABLE case_chunks DROP COLUMN IF EXISTS tsv;

DROP INDEX IF EXISTS legislation_sections_tsv_idx;
ALTER TABLE legislation_sections DROP COLUMN IF EXISTS tsv;
