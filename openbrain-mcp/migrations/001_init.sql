-- 001_init.sql — OpenBrain captures schema
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS captures (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_text    text        NOT NULL,
    summary     text        NOT NULL,
    keywords    text[]      NOT NULL DEFAULT '{}',
    source      text,
    source_url  text,
    lang        text,
    metadata    jsonb       NOT NULL DEFAULT '{}',
    fingerprint text        NOT NULL,
    embedding   vector(384) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Cosine-distance ANN index for semantic search
CREATE INDEX IF NOT EXISTS captures_embedding_idx
    ON captures USING hnsw (embedding vector_cosine_ops);

-- Fast "recent" listing
CREATE INDEX IF NOT EXISTS captures_created_at_idx
    ON captures (created_at DESC);

-- Dedup key: one row per fingerprint (idempotent capture)
CREATE UNIQUE INDEX IF NOT EXISTS captures_fingerprint_idx
    ON captures (fingerprint);
