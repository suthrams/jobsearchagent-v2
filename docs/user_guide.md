# Job Search Agent v2 — User Guide

End-to-end walkthrough: setup, first run, daily workflow, reading results, and troubleshooting.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Install](#2-install)
3. [Configure](#3-configure)
4. [Add Your Resume](#4-add-your-resume)
5. [Add LinkedIn Jobs (optional)](#5-add-linkedin-jobs-optional)
6. [Start the System](#6-start-the-system)
7. [Start a Workflow Run](#7-start-a-workflow-run)
8. [HITL: Select Jobs for Deep Review](#8-hitl-select-jobs-for-deep-review)
9. [Read the Deep Review](#9-read-the-deep-review)
10. [Career Advice](#10-career-advice)
11. [Interview Prep](#11-interview-prep)
12. [Tailor Your Resume](#12-tailor-your-resume)
13. [Daily Workflow](#13-daily-workflow)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

- Python 3.11+
- Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- Adzuna API credentials (free) — [developer.adzuna.com](https://developer.adzuna.com)

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

Edit `config/config.yaml` — key settings:

```yaml
search:
  titles:
    - software architect
    - principal engineer
    - Director of Engineering
  locations:
    - Atlanta, GA
    - Remote
  work_mode: [remote, hybrid]

salary:
  min_desired: 130000

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

Place your resume PDF at `resume.pdf` in the project root. The resume is parsed by Claude on first use and cached by SHA-256 hash — subsequent runs with the same PDF use the cache at no API cost.

---

## 5. Add LinkedIn Jobs (optional)

LinkedIn does not allow automated scraping. To include LinkedIn roles:

1. Browse LinkedIn and copy job URLs you want evaluated
2. Paste them into `data/linkedin_inbox.txt`, one per line
3. The agent processes and clears this file on the next run

---

## 6. Start the System

The system has two processes. Open two terminal windows.

**Terminal 1 — FastAPI backend:**
```bash
uvicorn app.api.main:app --reload
```

You should see:
```
ANTHROPIC_API_KEY detected — starting in live-agent mode (Phase 7)
Workflow graph built and cached.
INFO: Uvicorn running on http://127.0.0.1:8000
```

If you see `starting in mock mode` instead, your `ANTHROPIC_API_KEY` is not set or not loaded from `.env`.

**Terminal 2 — Streamlit UI:**
```bash
streamlit run app/ui/streamlit_app.py
```

Open `http://localhost:8501` in your browser.

---

## 7. Start a Workflow Run

In the Streamlit UI:

1. Go to the **Start** page
2. Upload your resume PDF (or select the cached version if uploaded before)
3. Review or adjust your search preferences
4. Click **Start Run**

The backend begins the workflow:
- Discovers jobs from Adzuna and LinkedIn
- Applies the pre-filter gate
- Researches each company (Research Agent — Haiku)
- Scores each job across your enabled tracks (Scoring Agent — Haiku, concurrent)

Monitor progress on the **Run Status** page. The run pauses at the job selection checkpoint once scoring completes.

---

## 8. HITL: Select Jobs for Deep Review

After scoring, the workflow pauses and asks you to select up to 3 jobs for deep review.

The UI shows:
- All scored jobs ranked by best score across tracks
- Per-track scores and match summary
- Company, location, salary, posting date

**Select 1–3 jobs** and click **Confirm Selection**. Only selected jobs incur deep review costs — unselected jobs are stored but receive no further LLM calls.

---

## 9. Read the Deep Review

For each selected job, the workflow runs:

1. **Resume Critic** (Sonnet) — section-by-section gap analysis
2. **Review Auditor** (Haiku) — quality check on the critic's review
3. Reflection loop repeats until quality threshold met (up to 3 rounds)

The deep review output shows:
- Overall fit summary
- Per-section resume analysis
- **Resume gaps** — experience you have but haven't documented clearly
- **Career gaps** — experience requirements you genuinely don't meet
- Suggested improvements
- Questions the agent couldn't resolve from your resume

At the **Deep Review Approval** checkpoint you can accept the review or request another round.

---

## 10. Career Advice

After all deep reviews complete, the Career Advisor (Sonnet) synthesizes findings across all selected jobs and produces:

- Track recommendation (which of IC / Architect / Management is your strongest fit right now)
- Positioning strategy per track
- Prioritized skill gaps to address
- Suggested application timeline

Runs once per workflow run, not per job.

---

## 11. Interview Prep

If a job's match score is ≥ 75 (or you request it at the checkpoint), the Interview Coach (Sonnet) prepares:

- Likely interview questions for this role
- Suggested answer frameworks drawing on your resume
- Topics to research before the interview
- Red flags or gaps to be ready to address

At the **Interview Prep Decision** checkpoint you can skip this step for any job to save cost.

---

## 12. Tailor Your Resume

At the **Tailoring** checkpoint, select a job and track and click **Tailor Resume**.

The Tailoring Agent (Sonnet) produces:
- Professional summary rewritten for this role and track
- Experience bullets selected and reworded for relevance
- ATS keywords from the job posting that match your background
- Identified gaps — labeled honestly, never fabricated

The **Fidelity Reviewer** (Haiku) validates every claim against your original resume before the draft is shown to you. Any unsupported claim is flagged.

At the **Tailoring Approval** checkpoint:
- **Accept** — draft is saved to `output/resumes/`
- **Reject** — draft is discarded; you can request a new one

---

## 13. Daily Workflow

Once set up, the typical daily routine:

```
Morning:
  1. Add any interesting LinkedIn URLs to data/linkedin_inbox.txt
  2. Start backend + UI (if not already running)
  3. Click Start Run
  4. ~5 min: scoring completes → select 1–3 jobs for deep review
  5. ~3 min: deep review completes → review and accept
  6. Read career advice and interview prep for new roles

As needed:
  7. Tailor resume for roles you decide to apply to
  8. Mark applied jobs at the Application Status Update checkpoint
```

**Cost estimate per run (10 jobs, typical):**

| Scenario | Estimated cost |
|---|---|
| Discovery + research + scoring only | ~$0.02–0.05 |
| Full run with deep review (3 jobs) | ~$0.05–0.15 |
| With tailoring for one job | ~$0.10–0.25 |

---

## 14. Troubleshooting

**Backend starts in mock mode**
- Check that `ANTHROPIC_API_KEY` is in your `.env` file in the project root
- Verify: `python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(bool(os.getenv('ANTHROPIC_API_KEY')))"`

**No jobs discovered**
- Check `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`
- Verify `config.yaml` has entries in `search.titles` and `scrapers.adzuna.locations`
- Adzuna free-tier quota: `(titles × locations) + remote_keywords` must be < 100/day

**LinkedIn jobs not appearing**
- Ensure URLs are in `data/linkedin_inbox.txt`
- Check backend logs for network errors on the LinkedIn fetch

**Workflow stuck at `waiting_for_user`**
- The workflow is paused at a HITL checkpoint — open the Streamlit UI and check the Run Status page for the pending decision

**Resume parse error**
- Ensure `resume.pdf` is in the project root
- To force a re-parse, delete the cached resume row from `data/v2.db`: `DELETE FROM resumes WHERE ...`

**Running the test suite**
```bash
python -m pytest tests/                   # full suite, mock mode (no API calls)
python -m pytest tests/ -m integration   # live smoke tests (requires .env)
```

**API reference**
The full REST API reference is at `http://localhost:8000/docs` (Swagger UI) when the backend is running.
