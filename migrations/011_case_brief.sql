-- Case briefs replace FILAC briefs (case-brief-plan.md).

-- Case lookup by name on the brief page ("Kazemi" -> R. v. Kazemi).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX cases_style_of_cause_trgm_idx ON cases USING gin (style_of_cause gin_trgm_ops);

-- Pasted and uploaded text: one row per distinct text, so the same text maps to one cached brief.
-- input_kind is 'decision' or 'description' (a user's notes or summary of a decision); corpus rows
-- leave it NULL, meaning decision.
ALTER TABLE cases ADD COLUMN content_hash text, ADD COLUMN input_kind text;
CREATE UNIQUE INDEX cases_user_text_hash_key ON cases (content_hash) WHERE source IN ('upload', 'pasted');

-- The FILAC brief is replaced outright: no old-format brief survives.
DELETE FROM filac_summaries WHERE prompt_version <> 'brief-v1';
