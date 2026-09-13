-- Phase 1: half-precision vectors (halves vector + index storage), chunk ordering,
-- and A2AJ metadata.
--
-- Vector indexes are dropped, not recreated: bulk loaders build them after loading
-- (scripts/ingest_a2aj.py), which is far faster than maintaining HNSW row by row.
-- Queries still work without the index, just as exact (slow) scans.

DELETE FROM cases WHERE source = 'smoke-test';

DROP INDEX IF EXISTS case_chunks_embedding_idx;
DROP INDEX IF EXISTS legislation_sections_embedding_idx;

ALTER TABLE case_chunks
    ALTER COLUMN embedding TYPE halfvec(1024) USING embedding::halfvec(1024),
    ADD COLUMN chunk_no integer,
    ADD COLUMN para_end integer;

DROP INDEX IF EXISTS case_chunks_case_id_idx;
CREATE INDEX case_chunks_case_id_chunk_no_idx ON case_chunks (case_id, chunk_no);

ALTER TABLE legislation_sections
    ALTER COLUMN embedding TYPE halfvec(1024) USING embedding::halfvec(1024);

-- Not every primary citation is neutral: pre-2000 SCC decisions are cited to the SCR.
ALTER TABLE cases RENAME COLUMN neutral_citation TO citation;
ALTER INDEX cases_neutral_citation_key RENAME TO cases_citation_key;

ALTER TABLE cases
    ADD COLUMN citation2 text,
    ADD COLUMN upstream_license text;
