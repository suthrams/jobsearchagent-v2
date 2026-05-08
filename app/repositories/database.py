"""SQLite database connection, schema initialisation, and retention purge utilities.

All timestamps in this system are produced exclusively by utcnow_iso() defined
here. No repository or service may generate timestamps by any other means —
this is the only way to guarantee consistent ISO 8601 UTC format across all
18 tables, which is required for correct string-sort ordering and purge queries.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("data/v2.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    state_json TEXT NOT NULL,
    user_id TEXT,
    resume_id TEXT,
    selected_job_id TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT,
    source_job_id TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    job_description TEXT,
    normalized_job_json TEXT,
    url TEXT,
    created_at TEXT NOT NULL,
    excluded INTEGER NOT NULL DEFAULT 0,   -- ADR-057: per-job pipeline-filter flag (1 = hidden / skipped)
    excluded_reason TEXT,                  -- ADR-057: optional free-text recall; never parsed
    excluded_at TEXT                        -- ADR-057: ISO 8601 UTC; null for unexcluded rows
);

CREATE TABLE IF NOT EXISTS resumes (
    id TEXT PRIMARY KEY,
    file_name TEXT,
    raw_text TEXT,
    raw_text_hash TEXT,
    parsed_profile_json TEXT,
    version INTEGER,
    is_active INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_scores (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    score_json TEXT NOT NULL,
    overall_score INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_rounds (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    round_number INTEGER,
    critic_output_json TEXT,
    audit_output_json TEXT,
    audit_score INTEGER,
    auditor_confidence INTEGER,
    stop_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_reviews (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    review_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS career_advice (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    advice_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interview_prep (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    prep_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tailored_resumes (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    tailored_json TEXT NOT NULL,
    fidelity_review_json TEXT,
    decision TEXT,
    decided_at TEXT,
    approved INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    report_json TEXT,
    report_markdown TEXT,
    report_file_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_decisions (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    decision_type TEXT,
    decision_value TEXT,
    payload_json TEXT,
    presented_at TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_config (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    config_key TEXT NOT NULL,
    config_value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS step_executions (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_name TEXT,
    event_type TEXT,
    input_summary TEXT,
    output_summary TEXT,
    status TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_name TEXT,
    provider TEXT,
    model TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_metrics (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    total_llm_calls INTEGER,
    total_tokens_input INTEGER,
    total_tokens_output INTEGER,
    total_cost REAL,
    total_duration_ms INTEGER,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_events (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    event_type TEXT,
    severity TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    memory_key TEXT,
    memory_value_json TEXT NOT NULL,
    confidence INTEGER,
    source_workflow_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_status     ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_started_at ON workflow_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_jobs_company             ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_title               ON jobs(title);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at          ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_job_scores_score         ON job_scores(overall_score);
CREATE INDEX IF NOT EXISTS idx_step_executions_run      ON step_executions(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_step_executions_started  ON step_executions(started_at);
CREATE INDEX IF NOT EXISTS idx_review_rounds_run        ON review_rounds(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_run         ON agent_events(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_created_at  ON agent_events(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run            ON llm_calls(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at     ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_type              ON memory_items(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_updated_at        ON memory_items(updated_at);
CREATE INDEX IF NOT EXISTS idx_security_created_at      ON security_events(created_at);
"""


def utcnow_iso() -> str:
    """Single source of truth for all timestamps in the system. Always UTC, always ISO 8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        # Migration: add raw_text_hash to resumes for existing DBs created before Phase 2
        try:
            conn.execute("ALTER TABLE resumes ADD COLUMN raw_text_hash TEXT")
        except Exception:
            pass  # column already exists
        # Migration: tailored_resumes columns added when on-demand tailoring shipped
        for col_ddl in (
            "ALTER TABLE tailored_resumes ADD COLUMN fidelity_review_json TEXT",
            "ALTER TABLE tailored_resumes ADD COLUMN decision TEXT",
            "ALTER TABLE tailored_resumes ADD COLUMN decided_at TEXT",
        ):
            try:
                conn.execute(col_ddl)
            except Exception:
                pass  # column already exists
        # Migration (ADR-057): per-job exclusion columns on the jobs table.
        # Same pattern as tailored_resumes above — safe on existing DBs.
        for col_ddl in (
            "ALTER TABLE jobs ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN excluded_reason TEXT",
            "ALTER TABLE jobs ADD COLUMN excluded_at TEXT",
        ):
            try:
                conn.execute(col_ddl)
            except Exception:
                pass  # column already exists
        # Migration (cost-tracking): cache token columns on llm_calls. Without
        # these the Cost Dashboard cannot tell whether prompt caching is
        # actually working — see docs/incidents/2026-05-07-cost-tracking-undercount.md.
        for col_ddl in (
            "ALTER TABLE llm_calls ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE llm_calls ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(col_ddl)
            except Exception:
                pass  # column already exists


def purge_old_data(db_path: Path = DEFAULT_DB_PATH, config: dict | None = None) -> dict[str, int]:
    """
    Delete rows older than configured retention windows.
    Returns {table_name: rows_deleted} for logging.
    Purge is explicit — never runs automatically.
    """
    retention = (config or {}).get("retention", {})
    workflow_days = retention.get("workflow_runs_days", 90)
    observability_days = retention.get("observability_days", 30)
    security_days = retention.get("security_events_days", 180)
    memory_days = retention.get("memory_items_days", 365)
    jobs_days = retention.get("jobs_days", 90)

    purge_plan = [
        ("workflow_runs",  "started_at",  workflow_days),
        ("jobs",           "created_at",  jobs_days),
        ("step_executions","started_at",  observability_days),
        ("agent_events",   "created_at",  observability_days),
        ("llm_calls",      "created_at",  observability_days),
        ("security_events","created_at",  security_days),
        ("memory_items",   "updated_at",  memory_days),
    ]

    results: dict[str, int] = {}
    with get_connection(db_path) as conn:
        for table, col, days in purge_plan:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {col} < datetime('now', ?)",
                (f"-{days} days",),
            )
            results[table] = cursor.rowcount
    return results
