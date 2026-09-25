-- Scenario fingerprints, so the same scenario (re-run, or re-uploaded) skips the LLM call.
-- Keyed by the text's hash, the prompt version and the configured primary model: changing either
-- bypasses old entries. `fingerprint` records which backend/model actually served it.
CREATE TABLE fingerprint_cache (
    text_sha256    text NOT NULL,
    prompt_version text NOT NULL,
    model          text NOT NULL,
    fingerprint    jsonb NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (text_sha256, prompt_version, model)
);
