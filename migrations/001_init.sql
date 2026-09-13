-- Phase 0 schema. Mirrors implementation-plan.md §3.
-- Embedding dimension 1024 = BAAI/bge-m3. Changing models means a new migration.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE cases (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    neutral_citation  text,
    style_of_cause    text,
    court             text,
    jurisdiction      text,
    decision_date     date,
    language          text,
    source            text NOT NULL,
    url_official      text,
    url_canlii        text,
    full_text         text,
    ingested_at       timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX cases_neutral_citation_key
    ON cases (neutral_citation) WHERE neutral_citation IS NOT NULL;
CREATE INDEX cases_court_idx ON cases (court);
CREATE INDEX cases_jurisdiction_idx ON cases (jurisdiction);
CREATE INDEX cases_decision_date_idx ON cases (decision_date);

CREATE TABLE case_chunks (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id    uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    para_no    integer,
    text       text NOT NULL,
    embedding  vector(1024),
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX case_chunks_case_id_idx ON case_chunks (case_id);
CREATE INDEX case_chunks_tsv_idx ON case_chunks USING gin (tsv);
CREATE INDEX case_chunks_embedding_idx ON case_chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE legislation (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    citation           text,
    title              text,
    jurisdiction       text,
    consolidation_date date,
    language           text,
    source             text NOT NULL,
    url_official       text,
    ingested_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX legislation_jurisdiction_idx ON legislation (jurisdiction);

CREATE TABLE legislation_sections (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    legislation_id uuid NOT NULL REFERENCES legislation (id) ON DELETE CASCADE,
    section_label  text,
    hierarchy_path text,
    text           text NOT NULL,
    in_force_start date,
    in_force_end   date,
    embedding      vector(1024),
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX legislation_sections_legislation_id_idx ON legislation_sections (legislation_id);
CREATE INDEX legislation_sections_tsv_idx ON legislation_sections USING gin (tsv);
CREATE INDEX legislation_sections_embedding_idx ON legislation_sections
    USING hnsw (embedding vector_cosine_ops);

-- Unified citation graph: case->case and case->statute.
CREATE TABLE citation_edges (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    src_type     text NOT NULL,
    src_id       uuid NOT NULL,
    dst_type     text NOT NULL,
    dst_id       uuid,
    citation_raw text,
    edge_kind    text NOT NULL,
    confidence   real
);

CREATE INDEX citation_edges_src_idx ON citation_edges (src_type, src_id);
CREATE INDEX citation_edges_dst_idx ON citation_edges (dst_type, dst_id);

-- Batch ingestion bookkeeping (no queue broker until concurrency demands one).
CREATE TABLE jobs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         text NOT NULL,
    status       text NOT NULL DEFAULT 'pending',
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    cursor       jsonb,
    error        text,
    started_at   timestamptz,
    finished_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX jobs_kind_status_idx ON jobs (kind, status);
