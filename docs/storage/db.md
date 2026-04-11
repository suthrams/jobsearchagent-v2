# storage/db.py — SQLite Database Layer

## Purpose

All persistence for `Job` objects goes through this class. Uses Python's standard library `sqlite3` module — no ORM dependency. Jobs are stored as a mix of typed columns (for fast querying and sorting) and JSON blobs (for flexible nested data like scores and salary).

## Design Decisions

### No ORM
`sqlite3` is used directly rather than SQLAlchemy or another ORM. This keeps the dependency list small and makes the SQL readable. The trade-off is slightly more verbose serialisation code in `_to_row()` and `_from_row()`.

### JSON Blobs for Nested Data
`salary_json` and `scores_json` store the entire `SalaryRange` and `TrackScores` objects as JSON strings. This avoids extra tables for a one-to-one relationship and keeps `INSERT` and `UPDATE` statements simple.

Additionally, individual score values (`score_ic`, `score_architect`, `score_management`, `score_best`) are stored as separate INTEGER columns. This allows:
- Fast `ORDER BY score_best DESC` without parsing JSON in SQLite
- The Streamlit dashboard to query scores directly via `pd.read_sql_query`

### Schema Migrations
New columns are added via `ALTER TABLE` without recreating the table. Two migration lists exist:
- `_MIGRATIONS` — tracks columns added to the `jobs` table after initial schema
- `_RUNS_MIGRATIONS` — tracks columns added to the `runs` table (e.g. token tracking columns added after the table was first introduced)

On startup, `_run_migrations()` checks existing columns for both tables and only applies the ones that are missing. Safe to run every time — idempotent.

### WAL Mode
```python
self._conn.execute("PRAGMA journal_mode=WAL")
```
Write-Ahead Logging allows concurrent reads while a write is in progress. The Streamlit dashboard can read while `main.py` is scoring, without locking errors.

## Public Interface

### `Database(db_path: str)`
Opens or creates the database file. Creates the jobs table if it doesn't exist. Runs migrations.

### Write Operations

| Method | Purpose |
|---|---|
| `insert_job(job) → Job` | Inserts a job. Silently ignores duplicate URLs (`INSERT OR IGNORE`). Sets `job.id` from the database. |
| `update_job(job)` | Updates all fields of an existing job. Requires `job.id` to be set. |
| `upsert_job(job) → Job` | Inserts if new (by URL), updates if exists. Convenience wrapper. |
| `insert_run(run_at, ...) → int` | Records one agent execution in the `runs` table. `run_at` must be captured **before scraping begins** so the dashboard `New Jobs` query (`WHERE found_at >= run_at`) correctly returns all jobs from that run. Returns the new run id. |

### Read Operations

| Method | Returns | Notes |
|---|---|---|
| `get_by_id(job_id)` | `Job?` | Primary key lookup |
| `get_by_url(url)` | `Job?` | Used for deduplication on insert |
| `get_by_title_company(title, company)` | `Job?` | Catches same job posted with different URLs |
| `get_by_status(status)` | `list[Job]` | Key for pipeline: `get_by_status(NEW)` = jobs to score |
| `get_all()` | `list[Job]` | All jobs, newest first |
| `count()` | `int` | Total job count |
| `get_runs()` | `list[dict]` | All run records, newest first. Used by the dashboard Run History view. |

### `close()`
Closes the database connection. Always called in `main.py`'s `finally` block.

## Schema

### `jobs` table

```sql
CREATE TABLE jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url              TEXT    NOT NULL UNIQUE,   -- deduplication key
    source           TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    company          TEXT    NOT NULL,
    location         TEXT,
    work_mode        TEXT,
    description      TEXT,
    salary_json      TEXT,                      -- SalaryRange as JSON
    scores_json      TEXT,                      -- TrackScores as JSON
    status           TEXT    NOT NULL DEFAULT 'new',
    posted_at        TEXT,                      -- ISO 8601
    expires_at       TEXT,
    found_at         TEXT    NOT NULL,
    applied_at       TEXT,
    score_ic         INTEGER,                   -- denormalised for fast queries
    score_architect  INTEGER,
    score_management INTEGER,
    score_best       INTEGER                    -- max(ic, architect, management)
)
```

### `runs` table

One row per `python main.py` execution. Used by the Run History dashboard view and by the **New Jobs** dashboard view (which queries `WHERE found_at >= run_at` to show only jobs found in the latest run).

> **Important:** `run_at` must be captured at the **start** of the run (before any scraping), not at the end. If `run_at` is later than `found_at` for the scraped jobs, the New Jobs view returns empty. `main.py` captures `run_started_at = datetime.utcnow()` as the very first action in `cmd_scrape_and_score()` and passes it to `insert_run(run_at=run_started_at, ...)`.

```sql
CREATE TABLE runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at        TEXT    NOT NULL,   -- ISO 8601 UTC timestamp
    jobs_scraped  INTEGER NOT NULL DEFAULT 0,  -- total returned by all scrapers
    jobs_new      INTEGER NOT NULL DEFAULT 0,  -- newly inserted (not duplicates)
    jobs_scored   INTEGER NOT NULL DEFAULT 0,  -- successfully scored by Claude
    jobs_skipped  INTEGER NOT NULL DEFAULT 0,  -- stale / no description / excluded
    batches       INTEGER NOT NULL DEFAULT 0,  -- number of Claude API batches
    est_cost_usd  REAL    NOT NULL DEFAULT 0.0, -- estimated cost (pre-run approximation)
    -- Actual token counts per operation from Anthropic API response metadata
    tokens_input_scoring    INTEGER NOT NULL DEFAULT 0,
    tokens_output_scoring   INTEGER NOT NULL DEFAULT 0,
    tokens_input_parsing    INTEGER NOT NULL DEFAULT 0,
    tokens_output_parsing   INTEGER NOT NULL DEFAULT 0,
    tokens_input_tailoring  INTEGER NOT NULL DEFAULT 0,
    tokens_output_tailoring INTEGER NOT NULL DEFAULT 0,
    actual_cost_usd         REAL    NOT NULL DEFAULT 0.0  -- cost from real token counts
)
```

`actual_cost_usd` uses the same Sonnet 4.6 pricing as the estimate ($3/M input, $15/M output) but is derived from real token counts returned by the API. The dashboard prefers `actual_cost_usd` over `est_cost_usd` whenever it is non-zero.

## Serialisation

`_to_row(job)` converts a `Job` to a flat tuple for the INSERT statement. Handles:
- Enum values: `job.source.value` → `"linkedin"`
- Optional datetimes: `.isoformat()` or `None`
- Pydantic models: `.model_dump_json()` or `None`

`_from_row(row)` reverses this. Handles:
- JSON blobs: `TrackScores.model_validate_json(row["scores_json"])`
- Enum reconstruction: `JobSource(row["source"])`
- Optional datetime parsing: `datetime.fromisoformat(row["posted_at"])`
