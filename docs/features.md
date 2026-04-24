# Job Search Agent — Features & Capabilities

A personal AI-powered job search assistant that scrapes job postings, scores them against your profile, and helps you tailor your resume — all from the command line and a browser dashboard.

---

## Table of Contents

1. [Job Scraping](#1-job-scraping)
2. [Smart Filtering](#2-smart-filtering)
3. [AI Scoring — Three Career Tracks](#3-ai-scoring--three-career-tracks)
4. [Dashboard](#4-dashboard)
5. [Job Exclusion](#5-job-exclusion)
6. [Resume Tailoring](#6-resume-tailoring)
7. [Run History & Cost Tracking](#7-run-history--cost-tracking)
8. [Profile Caching](#8-profile-caching)
9. [CLI Commands](#9-cli-commands)
10. [Configuration](#10-configuration)

---

## 1. Job Scraping

The agent pulls job postings from multiple sources on every run and deduplicates them automatically.

### Adzuna (automated)
- Searches a configurable list of job titles across multiple cities in a single run
- Separate keyword list for US-wide remote searches (no location filter)
- Configurable search radius in kilometres
- Free-tier quota guard: total calls = `(titles × locations) + remote_keywords` — quota commentary in config keeps you under the 100 calls/day limit

### LinkedIn (manual intake)
- LinkedIn blocks automated scraping, so the agent provides a simple manual intake
- Paste job posting URLs into `inbox/linkedin.txt` (one per line)
- The agent fetches each URL, extracts title/company/description, and clears the file so the same URL is never processed twice

### Ladders
- Scrapes Ladders.com, which focuses on $100k+ roles
- Runs automatically alongside Adzuna

### Deduplication
- Jobs are deduplicated by URL (exact match) and by title + company (case-insensitive) so the same role posted from multiple sources only enters the database once

---

## 2. Smart Filtering

Two filter layers remove noise jobs before any Claude API calls are made, saving quota and cost.

### Scraper-level filter (Adzuna)
Applied at scrape time, before the job is even inserted into the database:
- **Title relevance gate** — job must contain at least one keyword from `RELEVANT_TITLE_KEYWORDS` (e.g. "engineer", "architect", "director")
- **Title exclusion gate** — jobs with titles matching `EXCLUDED_TITLE_KEYWORDS` are dropped (e.g. property manager, leasing agent, project manager, sales engineer, intern)

### Scoring-level filter (before Claude)
Applied to all jobs (including LinkedIn/Ladders) before the scoring API call:
- **Title exclusion** — same exclusion list, catches any noise that bypassed the scraper filter
- **Tech description gate** — job description must contain at least one keyword from `TECH_DESCRIPTION_KEYWORDS` (e.g. "software", "cloud", "kubernetes") to confirm it's a technology role
- **Staleness gate** — jobs older than `staleness.max_days` (default 30 days) are skipped

Both layers import from a single shared module (`models/filters.py`) so the two lists are always in sync.

---

## 3. AI Scoring — Three Career Tracks

Each job is scored against your profile independently on three career tracks using Claude. Only tracks enabled in `config.yaml` are scored, saving API tokens.

| Track | Roles targeted |
|---|---|
| **IC** | Senior Engineer, Staff Engineer, Principal Engineer |
| **Architect** | Solutions Architect, Principal Architect, Enterprise Architect |
| **Management** | Senior Manager, Director of Engineering, VP of Engineering |

### How scoring works
- Jobs are chunked into batches of 10 and submitted to Claude concurrently — up to 3 parallel API calls at a time via `ThreadPoolExecutor`
- Each job receives a score of 0–100 per enabled track, a one-sentence summary, and a recommended flag (score ≥ 65)
- Salary penalty: Claude deducts 10 points and notes it in the summary if the posted salary is below your configured minimum
- Staleness note: Claude notes in the summary if a recommended job is more than 30 days old
- Scores are saved to the database immediately after each batch — if the run is interrupted, already-scored jobs are preserved
- One failed batch does not cancel others — it stays `NEW` and is retried on the next run

### Parallel batching
Up to `MAX_PARALLEL_BATCHES = 3` Claude calls run concurrently (safe for free-tier RPM). For a typical 30-job run this reduces wall-clock scoring time from ~45s to ~15s.

### Prompt caching
The Claude system prompt is byte-identical across all batches in a run (job count is passed only in the user message), so Anthropic's prompt caching applies to every batch including parallel ones — reducing input token cost by ~90% on cache hits.

---

## 4. Dashboard

A Streamlit browser dashboard for browsing, filtering, and acting on scored jobs.

```bash
streamlit run dashboard.py
```

### Views

| View | What it shows |
|---|---|
| **New Jobs** | All jobs found in the most recent run, split into Scored and Awaiting Scoring |
| **Top Matches** | All scored jobs ranked by best score across all tracks |
| **IC Track** | IC engineer roles ranked by IC score, with Claude's per-job summaries |
| **Architect Track** | Architect roles ranked by architect score |
| **Management Track** | Manager/Director/VP roles ranked by management score |
| **Companies** | Bar chart of top companies by best match score + drill-down table |
| **Run History** | Token usage, actual API cost, and jobs scored per run |

### Sidebar controls
- **Minimum score slider** — hide jobs below a threshold (default 60)
- **Search box** — filter any view by job title or company name
- **State multiselect** — filter any view to one or more US states (e.g. GA, TX). Only states present in the current scored jobs are shown as options. Empty selection means show all states.
- **Refresh button** — force a data reload (auto-refreshes every 30 seconds)

### Job card expander
Click any row in the Job Details section to expand a card showing:
- All three track scores side by side
- Company, location, salary, posted date, source
- Claude's one-sentence summary for each track
- Link to the original job posting
- Resume tailoring UI (see Section 6)
- Exclude button (see Section 5)

---

## 5. Job Exclusion

Remove jobs from all views when you've already applied, been rejected, or decided a role isn't a good fit — without deleting them from the database.

### Multi-select in tables
1. Click rows in any table (Top Matches, IC, Architect, Management) to select them — Shift/Ctrl for multi-select
2. A reason dropdown and **Exclude N job(s)** button appear below the table
3. Click Exclude — selected jobs disappear from all views immediately

### Per-job exclude in card expander
Each job card expander has a standalone "Exclude this job" button with the same reason dropdown, for one-at-a-time exclusion while reviewing job details.

### Exclusion reasons
- Not a good fit
- Applied elsewhere
- Rejected
- Not interested

Excluded jobs remain in the database with the reason stored (`excluded_reason` column) but are filtered out of every query so they never reappear across future runs.

---

## 6. Resume Tailoring

Claude rewrites your resume sections for a specific job and career track.

### From the CLI
```bash
python main.py --tailor 42
```
Choose the career track when prompted. The tailored output is saved to `output/resumes/<Company>_<Title>_<track>.txt`.

### From the dashboard
Inside any job card expander, select a track and click **Tailor Resume**. The result is shown inline and saved to disk.

### What Claude produces
| Section | Content |
|---|---|
| **Professional Summary** | 3–4 sentence opening written in first person, opening with title + years, highlighting relevant skills |
| **Highlighted Experience** | Per-role bullets selected and rewritten for relevance to this job |
| **ATS Keywords** | Keywords from the job posting that match your background |
| **Gaps to Address** | Requirements you don't clearly meet — honest and actionable |

### Mark as Applied
After tailoring, you can mark the job as APPLIED. This records `applied_at` in the database and changes the status, letting you track which roles you've submitted to.

---

## 7. Run History & Cost Tracking

Every `python main.py` run is recorded in the database and displayed in the **Run History** dashboard view.

### What's tracked per run
| Metric | Description |
|---|---|
| Jobs scraped | Total returned by all scrapers |
| Jobs new | Newly inserted (not duplicates) |
| Jobs scored | Successfully scored by Claude |
| Jobs skipped | Stale / no description / filtered out |
| Batches | Number of Claude API calls made |
| Estimated cost | Conservative pre-run estimate shown before scoring begins |
| Actual cost | Calculated from real token usage after the run |
| Scrape time | Wall-clock seconds for the scraping phase |
| Score time | Wall-clock seconds for the scoring phase |
| Total time | Full run wall-clock seconds |
| Avg batch latency | Mean Claude API call duration in seconds |
| Throughput | Jobs scored per second |

### Token tracking
Input and output tokens are tracked separately for each operation (resume parsing, job scoring, resume tailoring) and displayed in the Run History view. A cumulative actual cost line is plotted over time.

### Pre-run cost estimate
Before any API calls are made, the agent prints an estimated cost and asks for confirmation:
```
Scoring plan: 12 jobs in 2 batches of up to 10
Estimated API cost: ~$0.01 (Sonnet 4.6)

Continue? [y/N]:
```

---

## 8. Profile Caching

Your resume PDF is parsed by Claude once and cached as `data/profile.json`. On subsequent runs, the cached profile is used — no API call is made.

The cache is automatically invalidated when the resume PDF is modified (checked via file modification timestamp). To force a re-parse, delete `data/profile.json`.

---

## 9. CLI Commands

```bash
python main.py                        # scrape new jobs + score all unscored jobs
python main.py --list                 # show all jobs in the database with status breakdown
python main.py --tailor <ID>          # tailor resume for job ID (ID from --list or results table)
python main.py --dashboard            # scrape + score, then launch the Streamlit dashboard
python main.py --dashboard-only       # launch dashboard immediately without scraping
python main.py --purge                # delete scored jobs with best score < 75 (with confirmation)
python main.py --purge --threshold 80 # same, but with a custom cutoff
```

```bash
streamlit run dashboard.py        # launch dashboard standalone
python -m pytest tests/           # run the test suite (65 tests)
```

---

## 10. Configuration

All settings live in `config/config.yaml`. Copy `config/config.example.yaml` to get started.

### Key sections

| Section | What you configure |
|---|---|
| `search.locations` | Cities to display in results |
| `search.titles` | Job titles that drive Adzuna local searches (14 curated titles across IC, Architect, Management tracks) |
| `search.work_mode` | remote / hybrid / onsite |
| `salary.min_desired` | Minimum desired salary — Claude flags and penalises jobs below this |
| `tracks.ic/architect/management` | Enable/disable each scoring track |
| `scrapers.adzuna.locations` | Cities to search (one API call per title × location) |
| `scrapers.adzuna.radius_km` | Search radius around each city |
| `scrapers.adzuna.remote_keywords` | Keywords for US-wide remote searches |
| `claude.model.resume_parsing` | Model for resume parsing (default: `claude-sonnet-4-6`) |
| `claude.model.job_scoring` | Model for batch scoring (default: `claude-haiku-4-5-20251001`) |
| `claude.model.resume_tailoring` | Model for resume tailoring (default: `claude-sonnet-4-6`) |
| `staleness.max_days` | Jobs older than this are skipped during scoring (default: 30) |

### API keys required
Set in `.env` (never committed):
```
ANTHROPIC_API_KEY=sk-ant-...
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_api_key
```

---

## Feature Summary

| Capability | Status |
|---|---|
| Multi-source job scraping (Adzuna, LinkedIn, Ladders) | ✅ |
| Multi-city Adzuna search | ✅ |
| US-wide remote search | ✅ |
| Two-layer noise filtering (title + description) | ✅ |
| AI scoring across 3 career tracks | ✅ |
| Parallel batched scoring (3 concurrent Claude calls) | ✅ |
| Prompt caching for cost reduction (~90% on cache reads) | ✅ |
| Browser dashboard with 7 views | ✅ |
| Filter by US state across all dashboard views | ✅ |
| Multi-select job exclusion from dashboard | ✅ |
| Low-score purge (`--purge`) to keep DB laser-focused | ✅ |
| US state extraction from location string (all scrapers) | ✅ |
| Resume tailoring (CLI + dashboard) | ✅ |
| Run history with token + cost + latency tracking | ✅ |
| Phase timing and throughput metrics | ✅ |
| Profile caching (no repeat API calls for resume) | ✅ |
| SQLite persistence with automatic schema migration | ✅ |
| Pre-run cost estimate with confirmation gate | ✅ |
| Test suite (65 tests) | ✅ |
