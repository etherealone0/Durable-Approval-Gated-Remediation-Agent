-- Mock production database schema. Loaded automatically by the official
-- postgres image on first container start (docker-entrypoint-initdb.d).
-- See PROJECT_SPEC.md section 2.

CREATE TABLE IF NOT EXISTS records (
    id SERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO records (kind, payload) VALUES
    ('order', 'order-1001: shipped'),
    ('order', 'order-1002: pending'),
    ('order', 'order-1003: delivered'),
    ('session', 'session-a1: active'),
    ('session', 'session-a2: expired'),
    ('cache_entry', 'cache-key-42: stale');
