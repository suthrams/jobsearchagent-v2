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
| `exclude_jobs(job_ids, reason)` | Marks a list of job IDs as excluded, recording the reason string. Uses `executemany` for efficiency. Excluded jobs are filtered out of all dashboard queries. |
| `backfill_states() → int` | Populates the `state` column for all rows where `state IS NULL AND location IS NOT NULL`. Called on every `main.py` startup — idempotent, skips rows that already have a state. Returns the count of rows updated. Uses `extract_us_state()` from `models/filters.py`. |
| `delete_below_threshold(threshold, dry_run=False) → int` | Hard-deletes scored jobs where `score_best < threshold`. Jobs with `status IN ('applied', 'offer')` are always protected. Unscored (`score_best IS NULL`) rows are left untouched. `dry_run=True` returns the count without deleting. Used by `--purge` CLI command. |

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
    state            TEXT,                      -- US state abbreviation (e.g. 'GA', 'TX')
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
    score_best       INTEGER,                   -- max(ic, architect, management)
    excluded         INTEGER NOT NULL DEFAULT 0, -- 1 = excluded from all views
    excluded_reason  TEXT                        -- e.g. 'Rejected', 'Not a good fit'
)
```

`excluded`, `excluded_reason`, and `state` are added via `_MIGRATIONS` on first startup after the schema upgrade. All dashboard queries filter excluded rows out with `WHERE excluded = 0 OR excluded IS NULL`. The `state` column is populated at insert time (via `Job._fill_state` validator) and backfilled for existing rows by `backfill_states()` on startup.

### `runs` table

One row per `python main.py` execution. Used by the Run History dashboard view and by the **New Jobs** dashboard view (which queries `WHERE found_at >= run_at` to show only jobs found in the latest run).

> **Important:** `run_at` must be captured at the **start** of the run (before any scraping), not at the end. If `run_at` is later than `found_at` for the scraped jobs, the New Jobs view returns empty. `main.py` captures `run_started_at = datetime.now(tz=timezone.utc)` as the very first action in `cmd_scrape_and_score()` and passes it to `insert_run(run_at=run_started_at, ...)`.

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
    actual_cost_usd         REAL    NOT NULL DEFAULT 0.0, -- cost from real token counts
    -- Phase timing and throughput metrics (added via _RUNS_MIGRATIONS)
    elapsed_scrape_s        REAL    NOT NULL DEFAULT 0.0, -- wall-clock seconds for scraping
    elapsed_score_s         REAL    NOT NULL DEFAULT 0.0, -- wall-clock seconds for scoring
    elapsed_total_s         REAL    NOT NULL DEFAULT 0.0, -- total run wall-clock seconds
    avg_batch_latency_s     REAL    NOT NULL DEFAULT 0.0, -- mean Claude API call latency
    jobs_per_second         REAL    NOT NULL DEFAULT 0.0  -- scoring throughput
)
```

`actual_cost_usd` uses the same Sonnet 4.6 pricing as the estimate ($3/M input, $15/M output) but is derived from real token counts returned by the API. The dashboard prefers `actual_cost_usd` over `est_cost_usd` whenever it is non-zero.

The five timing columns are populated by `main.py` after scoring completes, reading `ScoringAgent.last_run_stats` for per-scoring-phase metrics and wrapping the full run in `time.perf_counter()` calls. All five are added via `_RUNS_MIGRATIONS` — rows from runs before the upgrade retain default `0.0` and are excluded from latency charts via the `has_latency_data` check in the dashboard.

### `insert_run()` Signature

```python
def insert_run(
    self,
    run_at: datetime,
    jobs_scraped: int = 0,
    jobs_new: int = 0,
    jobs_scored: int = 0,
    jobs_skipped: int = 0,
    batches: int = 0,
    est_cost_usd: float = 0.0,
    tokens_input_scoring: int = 0,
    tokens_output_scoring: int = 0,
    tokens_input_parsing: int = 0,
    tokens_output_parsing: int = 0,
    tokens_input_tailoring: int = 0,
    tokens_output_tailoring: int = 0,
    actual_cost_usd: float = 0.0,
    elapsed_scrape_s: float = 0.0,
    elapsed_score_s: float = 0.0,
    elapsed_total_s: float = 0.0,
    avg_batch_latency_s: float = 0.0,
    jobs_per_second: float = 0.0,
) -> int
```

## Serialisation

`_to_row(job)` converts a `Job` to a flat tuple for the INSERT statement. Handles:
- Enum values: `job.source.value` → `"linkedin"`
- Optional datetimes: `.isoformat()` or `None`
- Pydantic models: `.model_dump_json()` or `None`

`_from_row(row)` reverses this. Handles:
- JSON blobs: `TrackScores.model_validate_json(row["scores_json"])`
- Enum reconstruction: `JobSource(row["source"])`
- Optional datetime parsing: `datetime.fromisoformat(row["posted_at"])`
