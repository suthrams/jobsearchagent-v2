# Changelog

All notable changes are documented here, grouped by date.

---

## 2026-04-24

### Fixed

#### Dashboard Timestamp Parsing (mixed naive / tz-aware formats)
- `pd.to_datetime(..., utc=True)` coerces naive ISO timestamps (written by `utcnow()` before 2026-04-15) to NaT in this pandas version, while tz-aware `+00:00` strings (written by `now(tz=UTC)` after 2026-04-15) parsed correctly. This caused:
  - Run History: all timestamps for runs 1–14 displayed as blank / None
  - Run History: runs 15–18 were invisible entirely (NaT rows sorted away)
  - Top Matches / track tables: `Found` column blank for older jobs
- Fixed by extracting `_parse_utc(series)` helper in `dashboard.py` that strips the `+00:00` suffix before calling `pd.to_datetime`, so both timestamp formats land as identical naive UTC strings. Applied to `run_at` in `load_runs()` and `found_at` / `posted_at` in `load_jobs()` and `load_new_jobs()`.

---

### Added

#### US State Extraction and Filtering
- `extract_us_state(location)` added to `models/filters.py` — parses a 2-letter US state abbreviation from any unstructured location string. Handles `"Atlanta, GA"`, `"Austin, Texas"`, `"Washington, DC"`, multi-word states (longest-match-first), and returns `None` for `"Remote"` or non-US locations.
- `state: Optional[str]` field added to `Job` model with a `@model_validator(mode="after")` that auto-fills from `location` on construction — all three scrapers get state extraction for free with no code changes.
- `state TEXT` column added to the `jobs` table via `_MIGRATIONS` — populated on insert for new jobs; `backfill_states()` fills existing rows on startup.
- `Database.backfill_states()` — idempotent method that runs on every `main.py` startup to populate `state` for rows where it is `NULL`.
- **Dashboard state filter** — "Filter by state" multiselect in the sidebar. Applies to Top Matches, IC Track, Architect Track, Management Track views, and the scored-jobs table and job cards in the New Jobs view.
- **State column** in all job listing tables (Top Matches, IC/Architect/Management track tables, New Jobs scored, New Jobs unscored).
- **State column** in the Rich terminal table printed after each scoring run.

#### Low-Score Purge
- `Database.delete_below_threshold(threshold, dry_run=False)` — hard-deletes scored jobs where `score_best < threshold`. `status = 'applied'` and `status = 'offer'` rows are always protected. `dry_run=True` returns the count without deleting.
- `--purge` CLI flag — shows a count preview, requires explicit `y` confirmation, then calls `delete_below_threshold`. Default cutoff is 75.
- `--threshold N` CLI flag — override the purge cutoff (e.g. `--threshold 80`).
- `MIN_PERSIST_SCORE = 75` constant in `agents/scoring_agent.py` — jobs scored below 75 on all active tracks are deleted immediately after scoring and never reach `status=SCORED`. Eliminates the need for periodic cleanup of newly scraped jobs.
- `Database.delete_job(job_id)` — single-row hard delete by primary key, used by the scoring agent for immediate discard.

#### State Inference from County and City Names
- `_COUNTY_STATE` dict added to `models/filters.py` — maps ~100 unambiguous county/parish/borough base names to their state (e.g. `"fulton"→"GA"`, `"king"→"WA"`, `"harris"→"TX"`, `"hudson"→"NJ"`, `"hartford"→"CT"`). Covers all major US metro counties.
- `_CITY_STATE` dict added to `models/filters.py` — maps ~200 major US cities and NYC boroughs to their state (e.g. `"manhattan"→"NY"`, `"san francisco"→"CA"`, `"the woodlands"→"TX"`).
- `extract_us_state()` extended with two new fallback steps:
  - **Step 4** — regex-matches `"[Name] County/Parish/Borough"` substrings and looks up the base name in `_COUNTY_STATE`. Handles `"Atlanta, Fulton County"`, `"Seattle, King County"`, `"Jersey City, Hudson County"`, etc.
  - **Step 5** — splits the location on commas and checks each segment against `_CITY_STATE`. Handles `"Grand Central, Manhattan"`, `"Nob Hill, San Francisco"`, etc.
- Live database backfilled: NULL state rows reduced from ~800 to 63 (40 are bare `"US"` entries with no resolvable state; 23 genuinely uncoverable).

### Updated
- `docs/architecture.md` — updated dashboard data-flow diagram (state filter in sidebar), main-run flow (backfill step), component diagram (`extract_us_state` link), and mindmap (two new patterns: Location Normalisation, Focused Pipeline Management).
- `docs/features.md` — new CLI commands table, state filter in sidebar controls, three new rows in Feature Summary.
- `docs/dashboard.md` — sidebar controls table, data loading section, job cards section.
- `docs/main.md` — commands table, startup sequence, new `cmd_purge` in key functions, Purge command section.
- `docs/storage/db.md` — `state` column in schema, two new write-operations (`backfill_states`, `delete_below_threshold`).
- `docs/models/job.md` — `state` field in Metadata Fields table, `_fill_state` validator section.
- `docs/models/filters.md` — new US State Extraction section with examples and design notes.
- `docs/user_guide.md` — state filter in sidebar controls, new "Pruning low-quality matches" section.
- `CLAUDE.md` — updated Running the Agent command reference.

---

## 2026-04-17

### Fixed
- Update docs to match deprecated-API fixes from 2026-04-15: replace `datetime.utcnow()` references with `datetime.now(tz=timezone.utc)` and old Pydantic `class Config` snippet with `model_config = ConfigDict(...)` in `docs/models/job.md`, `docs/main.md`, `docs/storage/db.md`, `docs/architecture.md`, and `docs/blog_draft_patterns_v2.md`
- Blog draft `BEFORE` code block intentionally preserves `utcnow()` to illustrate the original bug

---

## 2026-04-15

### Fixed
- Replace deprecated `datetime.utcnow()` with `datetime.now(tz=timezone.utc)` across all files — `utcnow()` is deprecated in Python 3.12 and emits `DeprecationWarning` on Python 3.13 (`dashboard.py`, `main.py`, `models/profile.py`, `storage/db.py`, `tests/test_adzuna_scraper.py`, `tests/test_db.py`)
- Replace deprecated Pydantic v2 inner `class Config` with `model_config = ConfigDict(...)` in `models/job.py` and `models/profile.py`
