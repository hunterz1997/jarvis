-- Jarvis SQLite Schema

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'tool_result')),
    content TEXT NOT NULL,
    model_used TEXT,
    tool_name TEXT,
    tool_input TEXT,
    tool_result TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id, timestamp);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    task_type TEXT,
    description TEXT,
    status TEXT DEFAULT 'completed',
    result_summary TEXT,
    tools_used TEXT,
    model_used TEXT,
    duration_ms INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_task_log_timestamp ON task_log(timestamp);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    interval_minutes INTEGER,
    trigger_type TEXT NOT NULL DEFAULT 'recurring',
    run_at DATETIME,
    enabled INTEGER DEFAULT 1,
    next_run DATETIME,
    last_run DATETIME,
    run_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    task_name TEXT,
    content TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read, created_at);

CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp);

-- Default preferences
INSERT OR IGNORE INTO preferences (key, value) VALUES
    ('user_name', 'Prem'),
    ('user_role', 'Internal Auditor at RSM Astute Consulting'),
    ('user_location', 'Ahmedabad, Gujarat'),
    ('writing_tone', 'professional yet conversational'),
    ('food_preferences', 'biryani, Indian cuisine'),
    ('language', 'English');
