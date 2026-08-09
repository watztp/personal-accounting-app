CREATE TABLE IF NOT EXISTS worker_duty (
    worker_id TEXT PRIMARY KEY,
    worker_encrypt_id TEXT UNIQUE NOT NULL,
    user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'ready_for_review', 'finalized', 'failed', 'timeout')),
    step TEXT NOT NULL DEFAULT 'infer',
    retry_count INTEGER NOT NULL DEFAULT 0,
    image_path TEXT NOT NULL,
    draft_result TEXT,
    final_result TEXT,
    locked_by TEXT,
    locked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    session_timeout_sec INTEGER NOT NULL DEFAULT 600,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_worker_duty_status_created
ON worker_duty (status, created_at);
CREATE INDEX IF NOT EXISTS idx_worker_duty_updated
ON worker_duty (updated_at);
CREATE INDEX IF NOT EXISTS idx_worker_duty_encrypt
ON worker_duty (worker_encrypt_id);
