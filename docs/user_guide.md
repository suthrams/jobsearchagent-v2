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
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_api_key
```

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

The sidebar has a radio navigation with 13 items in three groups:

**Active Run**
- **Start New Run** — configure and launch a workflow
- **Monitor / HITL** — track progress, respond to HITL checkpoints
- **Run Report** — view the final report when the workflow completes

**Browse Results** *(read directly from `data/v2.db`)*
- **Top Matches** — all scored jobs by overall score
- **IC Track** — jobs sorted by technical score
- **Architect Track** — jobs sorted by architecture score
- **Management Track** — jobs sorted by leadership score
- **Deep Review Results** — per-job resume gap analysis and fit summary
- **Interview Prep** — per-job 7-day prep plan and likely interview topics

**Analytics**
- **Companies** — bar chart of top target companies by best match score
- **Run History** — log of all workflow runs with cost totals

**Sidebar controls** (apply to all Browse views):
- **Minimum score** slider — 0–100, default 60, step 5
- **Search** — filter by title or company name
- **Refresh data** — clears the data cache and reloads from `data/v2.db`

---

## 8. Start a Workflow Run

Select **Start New Run** in the sidebar. Fill in the form:

| Field | What to enter |
|---|---|
| **Resume ID** | `res-001` (default) — the ID the parser stored your resume under |
| **Roles** | Comma-separated job titles to search for, e.g. `Staff Engineer, Principal Engineer` |
| **Locations** | Comma-separated locations, e.g. `Remote` or `Atlanta, GA, Remote` |
| **Career track** | `ic`, `architect`, or `management` — sets which score column to optimize for |

Click **Start Workflow**.

On success the UI shows the `workflow_id` UUID and prompts you to switch to **Monitor / HITL**. The backend immediately begins:

1. Discovering jobs from Adzuna (up to 10 jobs per run)
2. Researching each company (Research Agent — Haiku)
3. Scoring each job against the selected career track (Scoring Agent — Haiku, concurrent)

---

## 9. Monitor Progress and Handle HITL Checkpoints

Select **Monitor / HITL** in the sidebar. Click **Refresh** to poll the backend for the latest status.

### Status indicators

| Symbol | Status | Meaning |
|---|---|---|
| 🔵 | `running` | Workflow is executing |
| 🟡 | `waiting_for_user` | Paused — action required (see below) |
| 🟢 | `completed` | Finished — report is available |
| 🔴 | `failed` | Unrecoverable error — check Errors section |

The view also shows the **current step** name (e.g. `score_jobs`, `await_job_selection`) and a metrics row: LLM calls used out of 100, estimated cost so far, and any error count.

### HITL Checkpoint 1 — Job Selection

When status is 🟡 `waiting_for_user` and the step is `await_job_selection`:

- A list of scored jobs appears, each showing title, company, and overall score
- Check the boxes next to **1–3 jobs** you want to deep-review
- Click **Submit Selection**

Only selected jobs proceed to deep review. Unselected jobs are stored in the database but receive no further LLM calls.

After submitting, the workflow resumes and runs for each selected job:

1. **Resume Critic** (Sonnet) — section-by-section gap analysis
2. **Review Auditor** (Haiku) — quality check on the critic's output
3. Reflection loop repeats until quality threshold met (up to 3 rounds)
4. **Career Advisor** (Sonnet) — cross-job career positioning synthesis
5. **Interview Coach** (Sonnet) — if match score ≥ 75
6. **Tailoring Agent** (Sonnet) — if you requested tailoring

### HITL Checkpoint 2 — Tailoring Approval

When status is 🟡 `waiting_for_user` and the step is `await_tailoring_approval`:

- The view shows **Fidelity Status** and **Recommendation** from the Fidelity Reviewer
- Click **View tailored draft** to inspect the full draft
- Choose one of three actions:

| Button | Effect |
|---|---|
| **Approve** | Accept the draft; workflow proceeds to report generation |
| **Request Revision** | Triggers another tailoring pass (within the 3-round limit) |
| **Reject** | Discard the draft; workflow proceeds to report without a tailored version |

After the final checkpoint, the workflow runs **Fidelity Review** and then **generates the report**.

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
