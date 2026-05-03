# Job Search Agent v2 — User Guide

End-to-end walkthrough: setup, starting the system, running a workflow, and reading results.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Install](#2-install)
3. [Configure](#3-configure)
4. [Add Your Resume](#4-add-your-resume)
5. [Add LinkedIn Jobs (optional)](#5-add-linkedin-jobs-optional)
6. [Start the System](#6-start-the-system)
7. [UI Navigation](#7-ui-navigation)
8. [Start a Workflow Run](#8-start-a-workflow-run)
9. [Monitor Progress and Handle HITL Checkpoints](#9-monitor-progress-and-handle-hitl-checkpoints)
10. [Read the Run Report](#10-read-the-run-report)
11. [Browse Results](#11-browse-results)
12. [Deep Review Results and Interview Prep](#12-deep-review-results-and-interview-prep)
13. [Analytics: Companies and Run History](#13-analytics-companies-and-run-history)
14. [Daily Workflow](#14-daily-workflow)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Prerequisites

- Python 3.11+
- Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- Adzuna API credentials (free) — [developer.adzuna.com](https://developer.adzuna.com)

The backend will start in **mock mode** if `ANTHROPIC_API_KEY` is missing — all agents are stubbed and no real LLM calls are made. Adzuna credentials are required only for automatic job discovery.

---

## 2. Install

```bash
git clone https://github.com/<your-username>/jobsearchagent-v2.git
cd jobsearchagent-v2
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

---

## 3. Configure

**Copy the example config:**

```bash
cp config/config.example.yaml config/config.yaml
```

`config/config.yaml` is gitignored. Key settings to edit:

```yaml
search:
  titles:
    - Staff Engineer
    - Principal Engineer
    - Solutions Architect
  locations:
    - Atlanta, GA
    - Remote

tracks:
  ic: true
  architect: true
  management: true
```

**Create `.env` in the project root:**

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...        # optional — required only if you route any agent to OpenAI
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_api_key
```

`OPENAI_API_KEY` is optional. If absent, OpenAI models are simply hidden from
the Settings UI dropdowns; Claude continues to serve every agent.

---

## 4. Add Your Resume

Place your resume PDF at `resume.pdf` in the project root. The resume is parsed by Claude on first use and cached by SHA-256 hash — re-running with the same file incurs no additional API cost.

The parsed resume is stored with the ID `res-001` in `data/v2.db`. Use this ID in the **Start New Run** form. If you update your resume file, the hash changes and the resume is re-parsed automatically.

---

## 5. Add LinkedIn Jobs (optional)

LinkedIn does not allow automated scraping. To include LinkedIn roles:

1. Browse LinkedIn and copy job posting URLs
2. Paste them into `inbox/linkedin.txt`, one per line
3. The agent processes and clears this file on the next run

---

## 6. Start the System

The system requires two processes running simultaneously. Open two terminal windows.

**Terminal 1 — FastAPI backend:**

```bash
uvicorn app.api.main:app --reload
```

Expected output:
```
ANTHROPIC_API_KEY detected — starting in live-agent mode (Phase 7)
Workflow graph built and cached.
INFO: Uvicorn running on http://127.0.0.1:8000
```

If you see `starting in mock mode`, check that `ANTHROPIC_API_KEY` is set in `.env`.

**Terminal 2 — Streamlit UI:**

```bash
streamlit run app/ui/streamlit_app.py
```

Open `http://localhost:8501` in your browser.

---

## 7. UI Navigation

The sidebar opens to **Workflow History** (the default landing) and gives you the
following views, top-down:

**Workflow-centric**
- **Workflow History** — all runs, click **Open** to drill into one
- **Workflow Detail** — unified per-run view: jobs, scores, deep review, advice,
  interview prep, **the settings used for that run**, and a "Limits & Constraints"
  section that flags where execution caps clipped results
- **Start New Run** — settings inline (threshold, max jobs, custom URLs) plus a
  textarea for line-by-line custom job URLs
- **Live Run Monitor** — activity feed for the currently running workflow
- **Run Report** — generated markdown report
- **Settings** — view and edit your default config (search criteria, threshold,
  salary, staleness, **per-agent provider + model**). Protected keys (hard
  limits, retention windows, prompt definitions) remain read-only.

**Cross-Run Analytics** *(read directly from `data/v2.db`)*
- **Top Matches** — scored jobs across all runs
- **IC / Architect / Management Track** — sorted by per-track score
- **Companies** — top target companies by best match score

**Sidebar controls**
- **Minimum match score** slider — 0–100, default 75, step 5. Same value drives
  the auto-selection of jobs for deep review (any track score ≥ this qualifies).
- **Search** — filter by title or company across browse views
- **Refresh data** — clears the data cache and reloads from `data/v2.db`

---

## 8. Start a Workflow Run

Select **Start New Run** in the sidebar. Fill in the form:

| Field | What to enter |
|---|---|
| **Resume ID** | `resume.pdf` (default) — the filename of your resume in the `data/` folder |
| **Roles** | Comma-separated job titles — pre-filled from your saved settings |
| **Locations** | Comma-separated locations — pre-filled from your saved settings |
| **Min match score** | Slider, defaults to 75 — any track score (tech / arch / lead) at or above this triggers deep review |
| **Max jobs** | Hard cap on jobs surfaced for processing (default 10) |
| **Custom job URLs** | Optional textarea — paste up to 25 URLs (LinkedIn, company career pages, ATS pages, etc.), one per line. They're scraped alongside Adzuna for this run. |
| **Save these settings as my defaults** | Persists the slider / max jobs / titles / locations as your defaults for future runs |

Click **Start Workflow**.

The backend runs end-to-end with no required user input:

1. **Job discovery** — Adzuna + your custom URLs (each custom URL is fetched and parsed via heuristics first, then via Claude if heuristics fall short; failures are logged per URL and skipped)
2. **Research** each company (Research Agent — Haiku)
3. **Scoring** across all three career tracks (Scoring Agent — Haiku, concurrent)
4. **Auto-select** up to 3 top-scoring jobs where any track ≥ your threshold
5. **Deep review** (Resume Critic + Review Auditor reflection loop, up to 3 rounds)
6. **Career advice** (Sonnet) per selected job
7. **Interview prep** (Sonnet) if any selected job's best track score ≥ threshold
8. **Report generation** as the final step

If no jobs clear the threshold, deep review and prep are skipped and the run goes straight to report generation. The "Limits & Constraints" section in **Workflow Detail** will flag this so you can lower the threshold or broaden search.

---

## 9. Monitor Progress

Select **Live Run Monitor** in the sidebar. Click **Refresh** to poll the backend for the latest status.

### Status indicators

| Symbol | Status | Meaning |
|---|---|---|
| 🔵 | `running` | Workflow is executing |
| 🟢 | `completed` | Finished — report is available, see **Workflow Detail** |
| 🟠 | `completed_with_errors` | Finished but some agents failed — check Errors |
| 🔴 | `failed` | Unrecoverable error — check Errors section |

The view shows the **current step**, a metrics row (LLM calls / 100, estimated cost, error count), and a per-agent activity feed. Custom URL extraction errors and 429 retry events appear here.

After completion, switch to **Workflow Detail** for the unified view of jobs, scores, deep review, advice, interview prep, the settings that were in effect for this run, and any execution-limit warnings.

---

## 10. Read the Run Report

Select **Run Report** in the sidebar. This view is only available when the workflow status is 🟢 `completed`.

The report renders as Markdown and includes:
- Summary of all selected jobs
- Deep review findings per job
- Career advice across tracks
- Interview prep highlights
- Any tailored resume sections (if approved)

Click **Download Markdown** to save a copy locally.

---

## 11. Browse Results

All Browse views read `data/v2.db` directly — they are available at any time, including during a run or between runs. Use the **Refresh data** button in the sidebar to reload after a run completes.

The sidebar **Minimum score** slider and **Search** box apply to all track views.

### Top Matches

Shows all scored jobs filtered by overall score ≥ minimum. Displays a summary row with total scored jobs, jobs above the threshold, and unique company count.

### IC / Architect / Management Track

Each track view shows jobs sorted by the track-specific score column:

| View | Score column |
|---|---|
| IC Track | `technical_score` |
| Architect Track | `architecture_score` |
| Management Track | `leadership_score` |

All track tables include: Job ID, Title, Company, Location, Score (progress bar), Summary, Recommended Next Action, and a direct link to the job posting.

---

## 12. Deep Review Results and Interview Prep

These views load results for a specific workflow run. If a workflow is active in your session, they load it automatically. Otherwise, enter a workflow ID manually.

### Deep Review Results

For each deep-reviewed job, an expandable section shows:
- **Fit Summary** — overall fit assessment
- **Positioning** — how to position yourself for this role
- **Recommended Action** — apply / hold / skip
- **Resume Gaps** — experience you have but haven't documented — can be addressed through tailoring
- **Career Gaps** — requirements you genuinely don't meet — labeled honestly, never fabricated

### Interview Prep

For each job where Interview Coach ran (match score ≥ 75), an expandable section shows:
- **Likely Topics** — subject areas likely to appear in interviews
- **7-Day Prep Plan** — day-by-day study and practice tasks
- **Areas to Defend** — resume weak points the interviewer may probe

---

## 13. Analytics: Companies and Run History

### Companies

Horizontal bar chart of the top 20 companies by best overall match score, filtered by the sidebar minimum score. The table below shows per-company job count and best score per track (Technical, Architecture, Leadership).

### Run History

Shows total workflow runs and cumulative estimated API cost across all runs. The full runs table below includes per-run status, job counts, LLM call counts, and cost.

---

## 13a. Picking a Provider and Model per Agent

Open **Settings** → **Agent Models** to see the per-agent assignment. Each
of the eight agents has a **Provider** dropdown (Claude / OpenAI) and a
**Model** dropdown filtered to the chosen provider. Indicative cost per
1K input + 1K output tokens is shown next to each model so you can see the
trade-off before saving.

Why you'd change one:

* **Claude is rate-limited.** Route `research_agent` and `scoring_agent`
  (the high-volume per-job agents) to OpenAI's `gpt-4o-mini` to keep
  workflows running while Claude cools off.
* **You want stronger reasoning on tailoring.** Switch
  `tailoring_agent` to `claude-opus-4-7` or OpenAI `o1` for one run, see if
  the output quality is worth the cost in the report's Cost Breakdown
  section, then revert.
* **You're cost-optimising.** Move every advisory agent to Haiku or
  `gpt-4o-mini` and watch total cost drop in the Workflow Detail rollup.

Two important constraints:

* Models must come from the registered list — you cannot type an arbitrary
  model name. The list lives in code (`app/providers/model_registry.py`) so
  cost tables and integration tests stay in sync.
* Saving a change **requires a backend restart** to take effect. In-flight
  workflows continue under whatever assignment they started with.

The cost rollup in **Workflow Detail → Cost Breakdown** and at the bottom
of the **Run Report** shows `provider · model · calls · in tokens · out
tokens · cost · avg latency` per agent so you can decide which agent is
worth re-routing next time.

---

## 14. Daily Workflow

Once configured, a typical session looks like:

```
1. (Optional) Add LinkedIn URLs to inbox/linkedin.txt
2. Start backend + Streamlit UI if not running
3. Start New Run — fill form, click Start Workflow
4. Switch to Monitor / HITL — refresh until scoring completes (~2–5 min)
5. HITL #1: select 1–3 jobs for deep review, click Submit Selection
6. Wait for deep review to finish (~3–5 min) — refresh periodically
7. HITL #2 (if tailoring was requested): review draft, approve or request revision
8. Switch to Run Report — read findings and download markdown
9. Browse Results for history across all runs
```

**Estimated cost per run (10 jobs):**

| Scenario | Estimated cost |
|---|---|
| Discovery + research + scoring only | ~$0.02–0.05 |
| Full run with deep review (3 jobs) | ~$0.05–0.15 |
| With tailoring for one job | ~$0.10–0.25 |

---

## 15. Troubleshooting

**Backend starts in mock mode**
- Verify `.env` exists in the project root with `ANTHROPIC_API_KEY=sk-ant-...`
- Check: `python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(bool(os.getenv('ANTHROPIC_API_KEY')))"`

**No jobs discovered**
- Check `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`
- Verify `config/config.yaml` has entries in `search.titles` and `scrapers.adzuna.location`

**Workflow stuck at `waiting_for_user`**
- This is expected — the workflow has paused at a HITL checkpoint waiting for your input
- Open **Monitor / HITL**, click **Refresh**, and submit the pending decision

**Monitor / HITL shows "No active workflow"**
- The session state is in-browser only; it resets on page reload or if Streamlit restarts
- You can still use Browse views — all historical data is in `data/v2.db`

**Resume parse error or wrong resume being used**
- Confirm `resume.pdf` is in the project root
- To force a re-parse, delete the cached row: `sqlite3 data/v2.db "DELETE FROM resumes WHERE id='res-001'"`

**No deep review results or interview prep data**
- These views require a workflow that completed a full deep review pass
- Check that status reached `completed` in **Monitor / HITL**

**API error in the UI (red banner)**
- Confirm the backend is running: `curl http://localhost:8000/docs`
- Check the uvicorn terminal for stack traces

**Running tests**
```bash
python -m pytest tests/                   # full suite, mock mode (no API calls)
python -m pytest tests/ -m integration   # live smoke tests (requires .env)
```

**API reference (Swagger UI)**

Available at `http://localhost:8000/docs` while the backend is running.
