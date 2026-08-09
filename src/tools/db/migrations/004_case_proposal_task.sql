ALTER TABLE case_operation_proposals ADD COLUMN task_id TEXT REFERENCES case_tasks(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_case_proposals_task ON case_operation_proposals(task_id, status);
