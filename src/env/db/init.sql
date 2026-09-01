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

-- Idempotency ledger for mutating tools (PROJECT_SPEC.md section 8): a
-- mutating tool checks its idempotency_key here before acting and no-ops
-- on repeat, since the same key can be resumed against after a process
-- kill and must not double-apply.
CREATE TABLE IF NOT EXISTS executed_actions (
    idempotency_key TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    args JSONB NOT NULL,
    result JSONB NOT NULL,
    compensation JSONB NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
