-- 通用企业事务 Case：持久化协作状态，而不是把状态隐含在一次对话中。

CREATE TABLE IF NOT EXISTS cases (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id),
    template_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL,
    context_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cases_owner_status
    ON cases(owner_user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS case_tasks (
    id              TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    task_key        TEXT NOT NULL,
    title           TEXT NOT NULL,
    domain          TEXT NOT NULL,
    status          TEXT NOT NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    details_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(case_id, task_key)
);

CREATE INDEX IF NOT EXISTS idx_case_tasks_case_status
    ON case_tasks(case_id, status);

CREATE TABLE IF NOT EXISTS case_artifacts (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    task_id     TEXT REFERENCES case_tasks(id) ON DELETE SET NULL,
    expert_id   TEXT NOT NULL,
    share_scope TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_case_artifacts_case
    ON case_artifacts(case_id, created_at);

CREATE TABLE IF NOT EXISTS case_operation_proposals (
    id                TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    artifact_id       TEXT REFERENCES case_artifacts(id) ON DELETE SET NULL,
    resource_id       TEXT NOT NULL,
    action            TEXT NOT NULL,
    summary           TEXT NOT NULL,
    parameters_json   TEXT NOT NULL DEFAULT '{}',
    approval_required INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'proposed',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_case_proposals_case_status
    ON case_operation_proposals(case_id, status);

CREATE TABLE IF NOT EXISTS case_events (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    actor_id    TEXT NOT NULL DEFAULT 'system',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_case_events_case
    ON case_events(case_id, created_at);
