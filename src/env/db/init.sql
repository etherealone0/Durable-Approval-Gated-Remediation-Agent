-- Mock production database schema. Loaded automatically by the official
-- postgres image on first container start (docker-entrypoint-initdb.d).

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

-- Idempotency ledger for mutating tools: a
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

-- Append-only audit trail: one row per state
-- transition, complete enough to reconstruct any run end-to-end from this
-- table alone (see src/audit/replay.py).
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    from_state TEXT,
    to_state TEXT,
    actor TEXT NOT NULL,
    action_proposed TEXT,
    risk_tier TEXT,
    approver_id TEXT,
    decision TEXT,
    idempotency_key TEXT,
    rationale TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_run_id_idx ON audit_log (run_id, id);

-- Latest-known-status index for the API: a
-- denormalized snapshot per run so GET /runs/{id} and GET
-- /runs/pending-approval don't need to touch the checkpointer directly.
-- The audit_log remains the source of truth for history; this is just a
-- queryable cache updated after every start/resume call.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    scenario_id TEXT,
    diagnosis TEXT,
    proposed_action TEXT,
    risk_tier TEXT,
    rationale TEXT,
    final_state TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);
