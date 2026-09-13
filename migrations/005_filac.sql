-- Phase 3: cached FILAC briefs. One brief per (case, prompt version, requested model);
-- changing the prompt or schema bumps the version, which invalidates the cache.

CREATE TABLE filac_summaries (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id        uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    prompt_version text NOT NULL,
    model          text NOT NULL,
    backend        text NOT NULL,
    summary        jsonb NOT NULL,
    verification   jsonb NOT NULL,
    usage          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (case_id, prompt_version, model)
);

-- Resolving authorities cited in a brief looks up reporter citations (e.g. [1999] 2 SCR 817).
CREATE INDEX cases_citation2_idx ON cases (citation2);
