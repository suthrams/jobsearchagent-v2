# agents/scoring_agent.py — Job Scoring Agent

## Purpose

Scores job postings against the candidate's profile across all active career tracks (IC, Architect, Management). Sends up to **5 jobs per Claude call** to minimise API cost while still getting individual scores for each job.

## Agentic Patterns

### 1. Batched Fan-Out
Instead of one Claude call per job, jobs are grouped into batches of 10 (`BATCH_SIZE = 10`). Each job in the batch gets an XML index tag (`<job index="0">`, `<job index="1">`, etc.) so Claude can return an array and scores can be mapped back even if Claude reorders items.

```
50 jobs →  5 batches of 10 →  5 Claude calls
vs.
50 jobs → 50 Claude calls   (without batching)
```

**Cache correctness:** `num_jobs` is intentionally absent from the system prompt. Including it caused a cache miss on the last batch of every run (e.g. "Score these 3 jobs" ≠ "Score these 10 jobs" → different cache key → full input charge). The count is passed in the user message only, keeping the system prompt byte-identical across all batches.

### 2. Pre-Filter Gate (Cheap Before Expensive)
Two filter stages run before any Claude call, eliminating irrelevant jobs:

```
Stage 1 — is_stale?         skip jobs posted > 30 days ago
Stage 2 — no description?   skip — Claude can't score without content
Stage 3 — excluded title?   skip sales, civil eng, Java roles, etc.
Stage 4 — tech description? at least one tech keyword must appear
                             catches hotel maintenance, plumbing, etc.
```

### 3. Multi-Track Scoring
A single Claude call returns scores for all three tracks simultaneously. The prompt lists active tracks; Claude returns `null` for disabled tracks. This avoids 3× the API calls that a per-track approach would require.

### 4. Crash-Safe Persistence
After each batch is scored, `db.update_job()` is called immediately — not once at the end. If the run is interrupted mid-batch, already-scored jobs are preserved and won't be sent to Claude again.

## Public Interface

### `ScoringAgent(client, loader, parser, tracks_config, salary_config)`

### `score_batch(jobs, profile, db=None, on_progress=None) → list[Job]`

| Parameter | Type | Purpose |
|---|---|---|
| `jobs` | `list[Job]` | Jobs to score — must already be in the database |
| `profile` | `Profile` | The candidate's parsed profile |
| `db` | `Database` (optional) | If provided, each job is saved immediately after scoring |
| `on_progress` | `Callable` (optional) | Called before each batch with `(batch_num, total_batches)` |

Returns the same list with `scores` and `status` populated on eligible jobs.

## Filter Keywords

Both filter lists live in **`models/filters.py`** — the single source of truth imported by both `ScoringAgent` and `AdzunaScraper`. Editing `models/filters.py` updates both gatekeeping layers simultaneously, preventing the drift that caused noisy jobs to reach Claude while the scraper dropped them.

### Excluded Titles (`EXCLUDED_TITLE_KEYWORDS`)
Titles containing these strings are skipped regardless of source (LinkedIn and Adzuna both pass through this gate):
- Sales: `presales`, `sales manager`, `sales engineer`, `account manager`, `business development`
- Non-tech management: `property manager`, `community manager`, `leasing`, `project manager`, `program manager`, `office manager`, `operations manager`, `fundraising`, `transcription`
- Non-software engineering: `electrical engineer`, `civil engineer`, `structural engineer`, `landscape architect`, `design specification`, `hvac`, `substation`, `medical`
- Junior/unrelated: `intern`, `internship`, `associate engineer`, `hotel`
- Language-specific: `java developer`, `java engineer`

### Required Description Keywords (`TECH_DESCRIPTION_KEYWORDS`)
At least one of these must appear in the description. Deliberately excludes broad words like `"technology"`, `"technical"`, `" it "`, and `"information technology"` that appear in HR, biotech, and office management roles:
- Languages: `software`, `python`, `javascript`, `typescript`, `.net`, `golang`, `rust`
- Cloud/infra: `cloud`, `aws`, `azure`, `gcp`, `kubernetes`, `docker`, `terraform`, `ci/cd`, `devops`, `platform engineering`
- Architecture: `api`, `microservice`, `distributed system`, `backend`, `frontend`, `saas`, `paas`, `application development`
- Data/AI: `data engineering`, `data pipeline`, `machine learning`, `artificial intelligence`, ` ai `, `llm`, `database`
- Leadership (scoped): `engineering team`, `software engineer`, `software development`, `digital transformation`
- IoT/edge: `iot`, `internet of things`, `mqtt`, `edge computing`, `embedded`, `connected devices`, `iiot`, `device management`, `telemetry`, `firmware`

## Claude Call Details

| Setting | Value |
|---|---|
| Prompt template | `prompts/score_job.md` |
| Operation | `job_scoring` |
| Max tokens | 3,500 (covers 10 jobs — ~300 tokens/score object) |
| Temperature | 0.1 (consistent scoring) |

## Data Flow

```
[list[Job]] + [Profile]
      │
      ▼
_score_chunk(chunk, profile)
  ├─ build jobs_block: XML with index tags
  ├─ PromptLoader.load("score_job", profile=..., jobs=..., tracks=...)
  ├─ ClaudeClient.call(system, user, "job_scoring")
  └─ ResponseParser.parse_list(raw, BatchJobScore)
       └─ returns list[BatchJobScore] mapped by job_index
              │
              ▼
        TrackScores(ic, architect, management)
              │
              ▼
       job.scores = track_scores
       job.status = SCORED
       db.update_job(job)
```

## Scoring Guidelines (from prompt)

| Score range | Meaning |
|---|---|
| 80–100 | Excellent fit — title, skills, seniority all match |
| 60–79 | Good fit — most requirements met, minor gaps |
| 40–59 | Partial fit — some relevant experience, notable gaps |
| 0–39 | Poor fit — significant mismatch |

`recommended = true` when `score >= 65`.

If the job salary is below `salary_config.min_desired`, Claude deducts 10 points and notes it in the summary.
