-- Phase 4: case->case citation graph for candidate expansion and authority signals.
-- Built by scripts/load_citations.py, which rebuilds it from scratch.

ALTER TABLE citation_edges ADD COLUMN source text;

CREATE UNIQUE INDEX citation_edges_case_pair_key
    ON citation_edges (src_id, dst_id)
    WHERE edge_kind = 'case_cites_case';

-- In-corpus inbound citations; a cheap proxy for how established an authority is.
ALTER TABLE cases ADD COLUMN cited_by_count integer NOT NULL DEFAULT 0;
