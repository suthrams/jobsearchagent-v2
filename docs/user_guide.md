# User Guide

This guide walks you through setting up and using the Job Search Agent from scratch — from first run to tailoring your resume for a job you want to apply to.

---

## Table of Contents

1. [First-Time Setup](#1-first-time-setup)
2. [Configure Your Search](#2-configure-your-search)
3. [Add Your Resume](#3-add-your-resume)
4. [Add LinkedIn Jobs (Optional)](#4-add-linkedin-jobs-optional)
5. [Run the Agent](#5-run-the-agent)
6. [Read the Results](#6-read-the-results)
7. [Browse the Dashboard](#7-browse-the-dashboard)
8. [Tailor Your Resume for a Job](#8-tailor-your-resume-for-a-job)
9. [Daily Workflow](#9-daily-workflow)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. First-Time Setup

### Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

### Create your `.env` file

Create a file named `.env` in the project root with your API keys:

```
ANTHROPIC_API_KEY=sk-ant-...
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_api_key
```

- **Anthropic API key** — get it at [console.anthropic.com](https://console.anthropic.com)
- **Adzuna credentials** — free account at [developer.adzuna.com](https://developer.adzuna.com)

> The `.env` file is gitignored and will never be committed.

---

## 2. Configure Your Search

Copy the example config and edit it:

```bash
cp config/config.example.yaml config/config.yaml
```

Open `config/config.yaml` and set the fields that matter most:

### Location and work mode

```yaml
search:
  locations:
    - Atlanta, GA     # your city
    - Remote
  work_mode:
    - remote
    - hybrid
```

### Salary target

```yaml
salary:
  min_desired: 150000   # Claude will flag jobs below this
  currency: USD
```

### Career tracks

Enable only the tracks you're actively pursuing. Disabled tracks are not scored — this saves API tokens.

```yaml
tracks:
  ic: true          # Senior / Staff / Principal Engineer
  architect: true   # Solutions / Principal Architect
  management: false # turn off if not looking for management roles
```

### Adzuna search keywords

These drive the job searches. Use titles you want to appear in results:

```yaml
scrapers:
  adzuna:
    keywords:
      - software engineer
      - solutions architect
      - engineering manager
    location: Atlanta, GA
    radius_km: 80
    remote_keywords:        # separate US-wide remote search
      - staff engineer
      - principal engineer
```

> **Tip:** `keywords` searches locally (within `radius_km` of `location`). `remote_keywords` searches US-wide with no location filter.

---

## 3. Add Your Resume

Place your resume PDF in the project root:

```
resume.pdf
```

The filename must be exactly `resume.pdf`. On the first run, Claude parses it into a structured profile and caches it at `data/profile.json`. On subsequent runs, the cache is used — no API call needed unless you update the PDF.

> If you update your resume, simply overwrite `resume.pdf`. The cache will be detected as stale and re-parsed automatically.

---

## 4. Add LinkedIn Jobs (Optional)

LinkedIn can't be scraped automatically. To include LinkedIn jobs:

1. Browse LinkedIn and find roles that interest you
2. Copy the job posting URL (e.g. `https://www.linkedin.com/jobs/view/123456789`)
3. Open `inbox/linkedin.txt` and paste the URL on a new line:

```
# Paste LinkedIn job URLs here, one per line
https://www.linkedin.com/jobs/view/123456789
https://www.linkedin.com/jobs/view/987654321
```

The scraper reads this file on each run, fetches the postings, and clears the file so the same URLs are not processed twice.

---

## 5. Run the Agent

```bash
python main.py
```

The run has three phases:

### Phase 1 — Scrape

```
Scraping jobs...
Found 47 jobs across all sources
12 new jobs added to database
```

Scrapers run in sequence. If one fails (network error, rate limit), the others continue. New jobs are deduplicated against the database by URL and by title+company.

### Phase 2 — Cost estimate and confirmation

```
Scoring plan: 12 jobs in 2 batches of up to 10
Estimated API cost: ~$0.01 (Sonnet 4.6)

Continue? [y/N]:
```

Type `y` to proceed. This is the only point where API tokens are spent. The estimate is conservative — actual cost is usually lower.

### Phase 3 — Scoring

```
  Scoring batch 1/3...
  Scoring batch 2/3...
  Scoring batch 3/3...
```

Each batch of up to 10 jobs is sent to Claude in one API call. Scores are saved to the database immediately after each batch — if the run is interrupted, already-scored jobs are preserved.

---

## 6. Read the Results

After scoring, a ranked table is printed:

```
╭──────────────────────────────────────────────────────────────────────╮
│                          Top Jobs by Score                           │
├────┬─────────────────────────────────┬──────────────────┬───┬────┬──┤
│ ID │ Title                           │ Company          │ IC│Arch│Rec│
├────┼─────────────────────────────────┼──────────────────┼───┼────┼──┤
│ 14 │ Principal Solutions Architect   │ Acme Corp        │ 72│ 88 │ Y │
│  9 │ Staff Software Engineer         │ TechCo           │ 85│ 61 │ Y │
│ 22 │ Director of Engineering         │ StartupXYZ       │ 55│ 58 │   │
╰────┴─────────────────────────────────┴──────────────────┴───┴────┴──╯
```

**Column guide:**
- **ID** — use this with `--tailor` to tailor your resume
- **IC / Arch / Mgmt** — score 0–100 per track (only enabled tracks shown)
- **Best** — highest score across all tracks
- **Rec** — Claude recommends applying (score >= 65)
- **Source** — which scraper found the job

**Score colour coding:**
- Green (75+) — strong fit
- Yellow (60–74) — good fit
- White (50–59) — partial fit
- Jobs below 50 are filtered out of the table

A full results file is also written to `output/logs/results.txt` with complete job details and Claude's summaries for each track.

---

## 7. Browse the Dashboard

For a richer view, open the Streamlit dashboard:

```bash
streamlit run dashboard.py
```

This opens a browser window at `http://localhost:8501`.

### Dashboard views

| View | What it shows |
|---|---|
| **Top Matches** | All scored jobs ranked by best score, with score metrics |
| **IC Track** | Senior/Staff/Principal Engineer roles, ranked by IC score |
| **Architect Track** | Solutions/Principal Architect roles, ranked by architect score |
| **Management Track** | Manager/Director/VP roles, ranked by management score |
| **Companies** | Bar chart of top companies + drill-down table |

### Sidebar controls

- **Minimum score slider** — hide jobs below a threshold (default 60)
- **Search box** — filter by job title or company name
- **Filter by state** — multiselect to narrow results to specific US states (e.g. GA, TX). Only states that appear in your scored jobs are listed. Leave empty to show all.
- **Found on or after** — date picker to show only jobs discovered on or after a chosen date. Defaults to 14 days ago on every page load. Move the date back to reveal older results.
- **Refresh button** — force a data reload (auto-refreshes every 30 seconds)

### Job cards

Click any job row to expand its card and see:
- All three track scores side by side
- Company, location, salary (if available), posted date
- Claude's one-sentence summary for each track
- Link to the original job posting

---

## 8. Tailor Your Resume for a Job

Once you've identified a job you want to apply to, use the `--tailor` command with the job's ID from the results table:

```bash
python main.py --tailor 14
```

You'll be asked which career track to optimise for:

```
Tailoring resume for: Principal Solutions Architect at Acme Corp
Which track? [1] IC  [2] Architect  [3] Management
> 2
```

Claude rewrites your resume sections for that specific job. The output is saved to `output/resumes/Acme_Corp_Principal_Solutions_Architect_architect.txt`:

```
TAILORED RESUME — Principal Solutions Architect at Acme Corp
Track: ARCHITECT
URL: https://...
======================================================================

PROFESSIONAL SUMMARY
----------------------------------------
Staff-level engineer with 14 years of experience designing distributed
systems and cloud-native architectures at scale. Deep expertise in AWS,
Kubernetes, and microservices with a track record of leading platform
modernisation programmes across enterprise clients. Proven ability to
align engineering strategy with business outcomes.

HIGHLIGHTED EXPERIENCE
----------------------------------------

Staff Engineer @ Previous Company
  • Designed a multi-region Kubernetes platform serving 200M requests/day
  • Led migration of 40 legacy services to AWS ECS, reducing ops cost 35%

ATS KEYWORDS
----------------------------------------
solutions architecture, AWS, Kubernetes, distributed systems, ...

GAPS TO ADDRESS
----------------------------------------
  • Salesforce ecosystem experience (listed as preferred)
  • Public sector / government client experience
```

Use this as your guide when updating your actual resume before submitting.

### Mark as Applied

After tailoring, you're asked:

```
Mark as APPLIED? (y/n):
```

Entering `y` records `applied_at` in the database and updates the job status to `APPLIED`. This lets you track which jobs you've actually submitted to.

---

## 9. Daily Workflow

Once the agent is set up, your daily workflow is:

```
1. Browse LinkedIn → paste interesting URLs into inbox/linkedin.txt
2. python main.py                    → scrape, score, print results
3. streamlit run dashboard.py        → browse and filter results visually
4. python main.py --tailor <ID>      → tailor resume for top matches
5. Submit application                → mark as APPLIED in the agent
```

### Checking what's in the database

```bash
python main.py --list
```

Shows all jobs in the database with a status breakdown:

```
Total jobs in database: 89
Status breakdown: {'new': 3, 'scored': 81, 'applied': 5}
```

### Pruning low-quality matches

After several runs, the database accumulates jobs that scored too low to be worth pursuing. Use `--purge` to remove them and keep the database focused:

```bash
python main.py --purge                # removes all scored jobs with best score < 75
python main.py --purge --threshold 80 # stricter cutoff — only keep 80+
```

The command shows a preview before asking for confirmation:

```
Purge preview
  Total jobs in DB : 312
  To be deleted    : 241  (score_best < 75, status not applied/offer)
  Will remain      : 71

Permanently delete 241 job(s)? This cannot be undone. [y/N]:
```

Jobs you've applied to (status `applied`) or received an offer for (status `offer`) are **never** deleted regardless of score.

### Re-running without scraping new jobs

If you just want to re-score or review existing jobs without hitting the scraper APIs, run `--list` to see what's already in the database, then use `--tailor` on any job.

---

## 10. Troubleshooting

### "No jobs to score" after scraping

- Check that `config.yaml` has valid `keywords` under `scrapers.adzuna`
- Check that `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are set in `.env`
- Adzuna free tier allows 100 calls/day — you may have hit the limit

### Scoring shows 0 jobs scored (all filtered)

The pre-filter gates are removing everything. Check `output/logs/run.log` for lines like:
```
INFO  Skipping non-tech description: Hotel Manager at...
INFO  Skipping excluded title: Sales Engineer at...
```
If legitimate jobs are being filtered, you may need to add keywords to `TECH_DESCRIPTION_KEYWORDS` in `agents/scoring_agent.py`.

### LinkedIn jobs have no description

LinkedIn's public HTML structure changes periodically. If `description` is consistently `None` for LinkedIn jobs, the CSS selectors in `scrapers/linkedin.py` may need updating. Check the selectors section in [scrapers/linkedin.md](scrapers/linkedin.md).

### "Config validation error" on startup

Your `config/config.yaml` has a missing or incorrectly typed field. The error message will name the exact field. Compare your file against `config/config.example.yaml`.

### Profile is not updating after resume change

Delete `data/profile.json` manually, then run `python main.py`. The agent checks file modification timestamps — if the timestamps are incorrect, delete the cache to force a re-parse.

### Claude returns invalid JSON

Rare, but possible. The run logs at `output/logs/run.log` include the raw response that failed to parse. This usually resolves on the next run. If it happens consistently, reduce the batch size (`BATCH_SIZE` in `agents/scoring_agent.py`) from 10 to 5.
