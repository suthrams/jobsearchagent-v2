# agents/scoring_agent.py — Job Scoring Agent

## Purpose

Scores job postings against the candidate's profile across all active career tracks (IC, Architect, Management). Sends up to **5 jobs per Claude call** to minimise API cost while still getting individual scores for each job.

## Agentic Patterns

### 1. Batched Fan-Out
Instead of one Claude call per job, jobs are grouped into batches of 5 (`BATCH_SIZE = 5`). Each job in the batch gets an XML index tag (`<job index="0">`, `<job index="1">`, etc.) so Claude can return an array and scores can be mapped back even if Claude reorders items.

```
50 jobs → 10 batches of 5 → 10 Claude calls
vs.
50 jobs → 50 Claude calls  (without batching)
```

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

### Excluded Titles (`EXCLUDED_TITLE_KEYWORDS`)
Titles containing these strings are skipped regardless of source:
- `presales`, `sales manager`, `sales engineer`, `account manager`
- `java developer`, `electrical engineer`, `structural engineer`
- `hotel`, `hvac`, and other non-IT disciplines

### Required Description Keywords (`TECH_DESCRIPTION_KEYWORDS`)
At least one of these must appear in the description:
- Core tech: `software`, `cloud`, `api`, `python`, `kubernetes`, `aws`, etc.
- Leadership: `engineering team`, `technical leadership`, `digital transformation`
- Domain: `machine learning`, `llm`, `data pipeline`, `ci/cd`
- IoT / edge: `iot`, `internet of things`, `mqtt`, `edge computing`, `embedded`, `connected devices`, `iiot`, `device management`, `telemetry`, `firmware`

## Claude Call Details

| Setting | Value |
|---|---|
| Prompt template | `prompts/score_job.md` |
| Operation | `job_scoring` |
| Max tokens | 2,000 (covers 5 jobs) |
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
