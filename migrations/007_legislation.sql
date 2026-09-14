-- Phase 5: section-grained federal legislation (Justice Laws XML + Constitution Acts HTML).
-- Loaded by scripts/ingest_legislation.py, which rebuilds these tables from scratch.

ALTER TABLE legislation
    ADD COLUMN code text,            -- 'I-2.5', 'SOR/2002-227', 'CONST-1982'
    ADD COLUMN kind text,            -- 'act' | 'regulation' | 'constitution'
    ADD COLUMN source_version text;  -- laws-lois-xml commit, or fetch date for HTML

CREATE UNIQUE INDEX legislation_jurisdiction_code_key ON legislation (jurisdiction, code);

ALTER TABLE legislation_sections
    ADD COLUMN section_no text,      -- top-level section label, e.g. '97'; section_label adds '(1)-(3)'
    ADD COLUMN chunk_no integer,
    ADD COLUMN marginal_note text,
    ADD COLUMN url_official text,
    ADD COLUMN cited_by_count integer NOT NULL DEFAULT 0;  -- decisions citing this section

CREATE INDEX legislation_sections_section_idx ON legislation_sections (legislation_id, section_no);
