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
7a. [Profiles (multi-user)](#7a-profiles-multi-user-adr-062)
8. [Start a Workflow Run](#8-start-a-workflow-run)
8a. [Advanced discovery & scoring options (opt-in)](#8a-advanced-discovery--scoring-options-opt-in)
9. [Monitor Progress](#9-monitor-progress)
10. [Read the Run Report](#10-read-the-run-report)
11. [Browse Results](#11-browse-results)
12. [Deep Review Results and Interview Prep](#12-deep-review-results-and-interview-prep)
13. [Tailored Resume Drafts](#13-tailored-resume-drafts)
14. [Analytics: Companies and Run History](#14-analytics-companies-and-run-history)
15. [Daily Workflow](#15-daily-workflow)
16. [Troubleshooting](#16-troubleshooting)

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

scoring:
  # ADR-071: which career tracks this profile is scored on. Subset of
  # ic / architect / management. Omit (or list all three) to score every track
  # like the Primary profile. Usually set per profile in Settings -> Scoring.
  tracks:
    - ic
    - architect
    - management
```

Most profiles fit one or two tracks, not all three (ADR-071). Inactive tracks are
not scored, do not trigger deep review, and are hidden in the results. You normally
set this per profile in **Settings → Scoring** rather than editing the YAML.

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

There are two ways to give a profile a resume:

- **In the UI (recommended):** the **Profiles → Add profile** wizard (and the
  default profile's onboarding) lets you upload a PDF directly. It is parsed by
  Claude, stored scoped to that profile, and set as the profile's active resume.
  See [section 7a](#7a-profiles-multi-user-adr-062).
- **On disk:** place your resume PDF at `resume.pdf` in the project root. On the
  first run for a profile that has no stored resume, enter `resume.pdf` in the
  **Start New Run** form and it will be parsed and stored under that profile.

Resumes are parsed once and cached by SHA-256 hash (per profile), so re-running
with the same file incurs no additional API cost. Once a profile has at least
one stored resume, **Start New Run** shows a resume **picker** instead of a text
box (the active resume is listed first). Each profile keeps its own active
resume — adding a resume to one profile never deactivates another's (ADR-062).

---

## 5. Add LinkedIn Jobs (optional)

LinkedIn does not allow automated scraping. To include LinkedIn roles:

1. Browse LinkedIn and copy job posting URLs
2. Paste them into `data/linkedin_inbox.txt`, one per line
3. The built-in LinkedIn scraper processes and clears this file on the next run

> Easier alternative: paste any job URLs (LinkedIn, company career pages, ATS
> links) straight into the **Custom job URLs** box on **Start New Run** (see
> [section 8](#8-start-a-workflow-run)) — no file editing required.

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

At the **top of the sidebar** is the **Profile** selector (ADR-062) — the
dropdown that picks whose search this is. Everything below (history, analytics,
cost, the resume picker) is scoped to the selected profile, and new runs are
tagged with it. The **＋ Add profile** button opens the onboarding wizard. See
[section 7a](#7a-profiles-multi-user-adr-062). On a fresh install there is one
profile, **Primary** (#0), which owns all pre-existing data.

The sidebar opens to **Workflow History** (the default landing) and gives you the
following views, top-down:

**Workflow-centric**
- **Workflow History** — all runs, **click any row** to open its Workflow Detail.
  The Run column shows the first role + first location (`+N` badges for the rest);
  the ID column is truncated — full UUIDs and full criteria appear on the detail
  screen.
- **Workflow Detail** — unified per-run view: jobs, scores, deep review, advice,
  interview prep, **the settings used for that run**, and a "Limits & Constraints"
  section that flags where execution caps clipped results
- **Start New Run** — settings inline (threshold, max jobs, custom URLs) plus a
  textarea for line-by-line custom job URLs
- **Live Run Monitor** — activity feed for the currently running workflow
- **Run Report** — generated markdown report
- **Settings** — view and edit the active profile's config (search criteria,
  threshold, **active scoring tracks** (ADR-071), salary, posting-age filter,
  **per-agent provider + model**). Each profile has its own overrides layered over the shared
  YAML defaults; a new profile starts on pure defaults. Protected keys (hard
  limits, retention windows, prompt definitions) remain read-only and shared by
  every profile.
- **Profiles** — manage profiles and run the **Add profile** onboarding wizard
  (ADR-062; see [section 7a](#7a-profiles-multi-user-adr-062)).

**Cross-Run Analytics** *(read through the API, scoped to the active profile; ADR-075)*
- **Top Matches** — scored jobs across all runs
- **IC / Architect / Management Track** — sorted by per-track score
- **Companies** — top target companies by best match score

**Sidebar controls**
- **Minimum match score** slider — 0–100, default 75, step 5. Same value drives
  the auto-selection of jobs for deep review (any **active** track score ≥ this
  qualifies; ADR-071).
- **Search** — filter by title or company across browse views
- **Refresh data** — clears the read cache and reloads from the API

---

## 7a. Profiles (multi-user, ADR-062)

The app can serve more than one job-seeker from one install — for example you and
a family member — each with their own resume, search defaults, config, learned
memory, cost view, and history. Use is **sequential**: pick a profile in the
sidebar, run searches as that profile, switch when you want to act as someone
else. There is no login.

> **Isolation is cooperative, not a security boundary.** The selector decides
> *which* profile's data a request reads and writes. With no authentication it
> does not *prevent* anyone with access to the app from selecting another
> profile. That is fine for a trusted personal/family tool; it is also exactly
> the seam where real authentication would attach later (see
> `docs/architecture/security.model.md` §4.1).

### Switching profiles

Pick a profile from the sidebar **Profile** dropdown. The whole UI re-scopes:
Workflow History, the cross-run analytics, the System Dashboard, and the Start New
Run resume picker all now show only that profile's data. The **Primary** profile
(id 0) owns everything that existed before profiles were introduced.

### Adding a profile (onboarding wizard)

Click **＋ Add profile** (or open the **Profiles** view). The wizard has three
steps; only the first is required:

1. **Identity** — a display **name** (required) and an optional **note** (a
   human-only label such as "new-grad SWE, west coast"; the system never acts on
   it). This creates the profile and assigns its id (1, 2, 3, ...).
2. **Resume** — upload a PDF. It is parsed and becomes the new profile's active
   resume. Skippable — you can add one later.
3. **Default search criteria** — roles and locations, saved as the profile's
   defaults so **Start New Run** pre-fills them. Skippable.

A profile created with just step 1 is fully valid; add a resume or criteria later
through the normal screens.

### Managing an existing profile

Open the **Profiles** view. Under **Manage an existing profile** are three
expanders:

- **Edit a profile (name / note)** — rename a profile or update its note.
- **Add a resume to a profile** — upload a new PDF for any profile. Becomes
  that profile's active resume.
- **Delete a resume from a profile** — pick a profile, pick one of its
  resumes, **tick the cascade-confirm checkbox** showing how many Resume
  Clinic reviews will also be removed, then click **Delete resume**. The
  cascade exists because clinic reviews reference a resume by id; if the
  resume is gone, the past-runs panel would show broken rows. Job-search
  workflow history and per-call cost data are **preserved** — only the
  resume row and its clinic reviews are removed.

  **Why delete then re-upload?** The parser caches the parsed profile by
  the PDF's text hash. Re-uploading the same PDF returns the cached
  profile instead of triggering a fresh parse. Deleting the resume row
  clears the cache for that profile, so the next upload of the same PDF
  runs the parser again — useful after a parser-prompt upgrade like
  ADR-067, when you want the new fields (GPA, honors, skill groups)
  populated on a previously-parsed resume.

---

## 8. Start a Workflow Run

Select **Start New Run** in the sidebar. The run is owned by the **active
profile**, uses that profile's saved defaults, and writes to that profile's
history. Fill in the form:

| Field | What to enter |
|---|---|
| **Resume** | A **picker** over the active profile's stored resumes (active one first). If the profile has no stored resume yet, this is a text box instead — enter `resume.pdf` to parse a file in the project root. |
| **Roles** | Comma-separated job titles — pre-filled from the active profile's saved settings. **These drive auto-discovery** (ADR-064): the search fetches these roles, not a fixed global list. |
| **Locations** | **One per line** — pre-filled from the profile's settings. Keep "City, State" on one line (e.g. `Atlanta, GA`); put `Remote` on its own line for a US-wide remote search. |
| **Min match score** | Slider, defaults to 75 — any **active** track score (the profile's subset of tech / arch / lead, ADR-071) at or above this triggers deep review |
| **Max jobs** | Hard cap on jobs surfaced for processing (default 10) |
| **Custom job URLs** | Optional textarea — paste up to 25 URLs (LinkedIn, company career pages, ATS pages, etc.), one per line. They're scraped alongside Adzuna for this run. |
| **Save these settings as my defaults** | Persists the slider / max jobs / titles / locations as your defaults for future runs |

Click **Start Workflow**.

The backend runs end-to-end with no required user input:

1. **Job discovery** — Adzuna + your custom URLs (each custom URL is fetched and parsed via heuristics first, then via Claude if heuristics fall short; failures are logged per URL and skipped)
2. **Research** each company (Research Agent — Haiku)
3. **Scoring** across the profile's active career tracks (Scoring Agent — Haiku, concurrent; inactive tracks are scored `null`, ADR-071)
4. **Auto-select** the top-scoring jobs where any **active** track ≥ your threshold — a job that clears the threshold only on an inactive track does not qualify (ADR-071)
5. **Deep review** (Resume Critic + Review Auditor reflection loop — both Haiku)
6. **Career advice** (Sonnet) per selected job
7. **Report generation** as the final step

**Interview prep is on demand by default (ADR-085).** The in-graph coach
auto-runs only when `scoring.auto_interview_prep` is enabled; otherwise you
trigger it per job from **Workflow Detail** after the run (the Sonnet coach is
the single most expensive agent, so it stays opt-in).

If no jobs clear the threshold, deep review is skipped and the run goes straight to report generation. The "Limits & Constraints" section in **Workflow Detail** will flag this so you can lower the threshold or broaden search.

> **Per-profile discovery + the entry-level caveat (ADR-064).** Each profile's roles
> drive its own Adzuna search, and relevance is derived from those roles — so a
> non-senior profile (e.g. an entry-level cybersecurity grad searching "Security
> Analyst, SOC Analyst") gets relevant results instead of the senior defaults.
> Two things to know: (1) the **scoring rubric is still calibrated for senior
> roles**, so entry-level matches score modestly — lower that profile's
> **Min match score** (Settings) so they qualify for deep review/tailoring; and
> (2) pasting specific postings as **Custom job URLs** still works and is a good way
> to target exact entry-level roles regardless of what Adzuna surfaces.
>
> **Targeting years of experience (ADR-065).** Start New Run has a per-profile
> experience window (saved when you tick "Save these settings as my defaults"):
> **Max years of experience** (e.g. `2` keeps 0-2 yr roles, drops "5+ years"
> postings) and **Min years of experience** (e.g. `5` excludes junior roles — for a
> *senior* profile), plus **Exclude senior roles** (drops senior/principal/staff/
> lead/director/manager/architect at the source and by title). `0` = that bound
> off; postings that don't state experience are kept; all default off. The min
> floor is noisier than the max cap (many JDs omit a stated floor), so pair it with
> a senior role search.

---

## 8a. Advanced discovery & scoring options (opt-in)

All of these are **off by default** and live in the **Start New Run** form (most
are also under **Settings**). Tick **"Save these settings as my defaults for
future runs"** to persist them per profile. They trade a little setup for tighter,
cheaper results.

### Pick which jobs to score (manual selection, ADR-060)

- Tick **"Let me pick which jobs to score (review before scoring)"**. Discovery
  casts a wider net (up to the **Discovery net width**, default 50) and the run
  **parks before scoring** instead of auto-scoring everything.
- Open **Workflow Detail** for that run — its status shows `awaiting_scoring_selection`
  (🟡). Under **🧭 Select jobs to score**, tick the jobs worth the research +
  scoring spend and submit. Only those are scored; the rest are skipped at no cost.
- Use it when discovery is noisy and you'd rather eyeball titles/companies before
  paying to score.

### Reasoning relevance filter (ADR-079)

- Tick **"Reasoning relevance filter (drop mismatches before scoring)"**. One
  cheap LLM pass reasons over every discovered job and drops clear seniority/role
  mismatches **before** scoring, so you don't pay to score the noise.
- It is **profile-relative**: too-senior roles are dropped for an early-career
  profile, too-junior roles for a senior one. It widens discovery to triage from,
  and if the pass fails for any reason it **keeps every job** (it never silently
  loses a run).
- This is the automatic counterpart to manual selection — let the model triage
  instead of doing it by hand. Don't enable both; manual selection takes
  precedence.

### Drop stale postings (posting age, ADR-080)

- Set **"Max posting age in days"** (e.g. `30`). Postings older than that are
  dropped at discovery, before the relevance filter and scoring — stale postings
  often have dead apply links.
- Deterministic, no extra API cost. Postings with no parseable date are kept;
  `0` = off. The posting date is shown on each job's detail.

### Experience window (ADR-065)

- Covered in [section 8](#8-start-a-workflow-run): **Min / Max years of
  experience** and **Exclude senior roles** narrow discovery to your career stage.

### ATS-direct sources — Greenhouse & Lever (ADR-081)

- Source-of-truth employer feeds: listings are live and the apply link is the
  employer's own ATS page (no dead-link / rate-limit issues that aggregators can
  have).
- **Configured in `config/config.yaml`** (not the run form), per company:

  ```yaml
  scrapers:
    greenhouse:
      companies: [stripe, figma]   # board tokens from boards.greenhouse.io/<token>
    lever:
      companies: [leverdemo]       # slugs from jobs.lever.co/<slug>
  ```

- An empty list = off. Find a company's token/slug in its careers URL. These run
  alongside Adzuna on every run; title relevance uses the run's roles.

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

### Cancelling a run

While a run is `running`, **Live Run Monitor** shows a **Cancel** button.
Cancellation is **cooperative** (ADR-083): the run stops at the next node
boundary rather than halting instantly mid-step, so the status moves to
`cancelling` and then `cancelled`. Work already finished (discovered jobs,
scores) is preserved and visible in **Workflow Detail**.

After completion, switch to **Workflow Detail** for the unified view of jobs, scores, deep review, advice, interview prep, the settings that were in effect for this run, and any execution-limit warnings.

---

## 10. Read the Run Report

Select **Run Report** in the sidebar. This view is only available when the workflow status is 🟢 `completed`.

The report renders as Markdown and includes:
- Summary of all selected jobs
- Deep review findings per job
- Career advice across tracks
- Interview prep highlights

Tailored resume drafts are generated on demand from **Workflow Detail** (see [section 13](#13-tailored-resume-drafts)) and are not part of the auto-generated report.

Click **Download Markdown** to save a copy locally.

---

## 11. Browse Results

All Browse views read through the FastAPI backend (ADR-075: the UI never opens the database directly) — they are available at any time, including during a run or between runs. Use the **Refresh data** button in the sidebar to reload after a run completes.

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

If a track is not active for the current profile (ADR-071), its view shows a "not
active for this profile" notice instead of an empty table — enable it under
**Settings → Scoring** if you want it.

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

Interview prep is **on demand by default** (ADR-085) — trigger it per job from
**Workflow Detail** (or enable `scoring.auto_interview_prep` to have the in-graph
coach run automatically for selected jobs that clear the threshold). For each job
where the Interview Coach ran, an expandable section shows:
- **Likely Topics** — subject areas likely to appear in interviews
- **7-Day Prep Plan** — day-by-day study and practice tasks
- **Areas to Defend** — resume weak points the interviewer may probe

---

## 13. Tailored Resume Drafts

Tailoring runs **on demand**, per job, after the workflow finishes. There is no HITL pause for tailoring during the run — pick whichever deep-reviewed jobs are worth the effort once you've read their reviews, and generate drafts for those.

### Where to find it

**Workflow History** → click any completed run → scroll to **✨ Prep — tailored resume drafts** at the bottom of the Workflow Detail page. There is one expandable section per deep-reviewed job (`selected_jobs`).

Inside each job's expander:

- **✨ Generate new draft** — runs Tailoring Agent + Fidelity Reviewer. Takes 5-15s and ~6 LLM calls (~$0.01-0.02). Each click creates a brand-new draft; previous drafts stick around so you can compare.

### What a draft contains

When a draft renders you'll see four blocks, top to bottom:

**1. Strategy for this draft** — a callout with the agent's positioning thesis for the whole draft. Sentence one is the thesis (`"Positioning you as <role-shape> who <strongest hook from JD>."`), followed by 2-3 concrete moves anchored to specific JD signals. This is the line to read before deciding whether to spend time on the rest.

**2. Estimated impact (directional, not a re-score)** — per-track lift derived from the structure of the suggestions:
- 🟢 Likely lift, 🟡 small lift, ⚪ neutral
- Up to four example tokens added per track (e.g. `kubernetes`, `prometheus`)
- Footer for freed bullets (`remove` count) and unclosed gaps (`gap` count)

This is a cheap heuristic, not a re-score — it tells you which tracks the draft is moving toward, not what number the agent would assign. Re-scoring with the same agent that scored the original creates a self-fulfilling prophecy (see ADR-056 addendum #3).

**3. Fidelity flags** (collapsed unless there are violations) — unsupported claims, fabricated metrics, inflated scope, length overruns, generic rationale, missing strategy summary, etc. If the Fidelity Reviewer's `approval_recommendation` is `"revise"` or `"reject"`, the header at the top of the draft tells you so.

**4. Section-grouped diffs** — suggestions grouped by resume section in resume order:
- Headline (positioning tagline below the name)
- Summary
- Experience entries in resume order
- Skills additions

Each section header shows the count and the per-section word delta (e.g. `3 suggestions · 47w → 41w (-6w) · 1 remove`) so you see the page-budget impact at a glance.

### What's in each suggestion

Per bullet:
- **Original** vs **Suggested** side-by-side
- **Claim type** badge:
  - 🟦 `reword` — same meaning, sharper for this job
  - 🟩 `emphasize` — promotes a strong point that was buried
  - 🟧 `gap` — surfaces missing experience; never rewritten as if present (you decide whether to address in cover letter / interview)
  - 🟥 `remove` — the bullet doesn't pull weight for this job; deleting it frees space for higher-value rewrites elsewhere
- **Length:** `24w → 19w (-5w)` — the page-budget delta. Per the contract (ADR-056), `suggested_text` word count must fall in `0.85x..1.05x` of `original_text` for summary / experience / skills, relaxing to ±3 words for headlines. The Fidelity Reviewer rejects bullets that overflow OR collapse.
- 📎 **Evidence** — the exact resume line that justifies the suggestion. No suggestion ships without evidence.
- 💡 **Why for this role** — one sentence tying the change to a specific JD signal (a stated requirement, named technology, responsibility). Generic praise like "stronger phrasing" gets rejected.

### Decisions

Three buttons at the bottom of every undecided draft:
- **✅ Approve** — record this draft as the one you used. Does NOT modify your resume file (you paste the changes manually).
- **✏️ Request revision** — flag the draft for follow-up. Click **Generate new draft** to produce another attempt.
- **🚫 Reject** — discard.

Decisions are persisted in `tailored_resumes.decision`. You can iterate as many times as you like; drafts accumulate per job, newest first.

### Reading the strategy summary

If sentence one opens with hedging (`"This draft attempts to..."`) or generic praise (`"Strong overall fit..."`) instead of a positioning thesis, the Fidelity Reviewer flags it and recommends `revise`. If you see this pattern, click **Generate new draft** — the v5 prompts catch it but a single rerun with the same context usually produces better positioning.

### Page-budget mental model

The whole point of the per-bullet length rule is that you can paste suggestions into your resume and the page count doesn't change. If you adopt every reword + every remove suggested in a draft, the resume should occupy the same number of lines as before. That's the contract.

If a section's suggestions don't fit your style, skip them — but don't add length back by combining a `reword` with extra context. The rule exists because real resumes get reflowed onto a third page when bullets grow, and that's the most common reason tailored content doesn't actually get used.

---

## 13a. Resume Clinic (ADR-066)

The funnel (Sections 8–13) is built around scored jobs. If you don't have
a senior background yet, those flows often return "nothing qualified" and
the resume-facing agents (critique, advice, tailoring) stay locked behind
the funnel.

The **Resume Clinic** is a second surface that runs on your **resume alone**,
with no JD. Open **Resume Clinic** in the sidebar.

### What it gives you

1. **Quality scorecard** — one rating (`strong / adequate / needs_work`) per
   dimension: structure & ordering, impact & quantification, clarity, ATS
   formatting, consistency, length fit, seniority framing. Each dimension
   comes with specific findings and concrete fixes.
2. **Role / track alignment** (optional) — when you enter a target role
   (and optionally a track: IC / Architect / Management) the reviewer adds
   an alignment read: fit summary, missing skills, missing keywords,
   suggested certifications, suggested projects, and items already on your
   resume that should be emphasized harder.
3. **Reorganization plan** — a proposed top-down section order plus a list
   of moves (`move / cut / promote`) with rationale.
4. **Rewrites** — bullet-level suggestions with `claim_type` (`restate`,
   `reorder`, `quantify`, `reframe`). Every rewrite is **evidence-bound**:
   the reviewer must cite something already in your resume. Missing
   experience is labelled as a gap in the alignment block, never
   rewritten as if present. The same Fidelity Reviewer that polices
   tailoring runs on the clinic's rewrites too — fabrication is caught
   automatically.

### How to use it

1. Pick a resume (your active one is preselected).
2. Optionally type a **target role** (free text — "entry-level security
   analyst", "principal platform engineer", anything). The form prefills
   from your profile's first saved role; clear the field for quality-only
   mode.
3. Optionally pick a **target track**.
4. Toggle **seniority-aware feedback** on if you want the review calibrated
   to your career stage (early-career: projects/education forward;
   senior+: scope and outcomes).
5. Click **Run clinic**. The review lands in the right panel — quality
   scorecard, alignment, reorganization, rewrites, fidelity verdict.
6. Decide: **Approve** locks the review as-is. **Revise** asks for another
   pass (the next clinic run is a fresh row). **Reject** discards.
   To refine a draft interactively, use the **chat-revise loop** (ADR-068):
   chat with the reviewer turn by turn (bounded to 25 turns with a running
   cost meter); a human-edited draft is saved as the `edit` decision and is
   trusted as-authored, not re-reviewed (ADR-059).

### Past runs

Below the live panel is a list of every past clinic run for the active
profile, newest first. Click an expander to see its summary and tap
**Load into results pane** to re-render an earlier one.

### Cost

The clinic is one reviewer call plus (when there are rewrites) one
Fidelity Reviewer call. It writes a lightweight `resume_clinic`
`workflow_runs` row so the **System Dashboard** attributes clinic spend
to the active profile correctly.

### Export the final resume

Below the decision controls is an **Export the final resume** panel. The
renderer is **decision-aware** — what you get depends on which decision
button you clicked:

- **Approve** → applies the agent's overhaul (reorganization + rewrites)
  to your parsed resume and renders that.
- **Edit** → uses the human-authored draft you saved via the chat-revise loop
  (ADR-068); the edited draft is trusted as-authored and not re-reviewed (ADR-059).
- **Reject** → renders your original parsed resume unchanged.
- *No decision yet* → renders a preview of the agent's overhaul with a
  small italic note at the bottom saying so.

Six formats are available:

| Format | Good for |
|---|---|
| **Markdown** (`.md`) | Review, paste into Google Docs / Notion / a markdown editor |
| **Plain text** (`.txt`) | ATS systems that strip formatting; raw copy-paste |
| **HTML** (`.html`) | Preview in a browser; print-to-PDF via the browser for styled output |
| **JSON Resume** (`.json`) | Round-trip into other resume tools (jsonresume.org schema) |
| **Word** (`.docx`) | ATS systems that want a Word document; many employers list this format |
| **PDF** (`.pdf`) | Final delivery; what most employers actually open |

Pick a format from the selector. Markdown / plain text / JSON / HTML have
an inline preview expander; click **Download** to save the file.

The renderer is **deterministic** — no LLM call. Every export of the same
decision-applied state produces byte-identical output. The agent's
evidence-bound rules apply through the renderer too: placeholders like
`[N]+ services` or `[X]%` survive verbatim (you fill them in), nothing the
agent didn't rewrite is touched, and missing experience stays labelled as a
gap rather than fabricated.

### What gets preserved on parse (ADR-067)

When you upload (or re-upload) a resume, the parser captures:

- **GPA** verbatim under each Education entry (e.g. `"3.9/4.0"`,
  `"First Class"`)
- **Academic honors** as a free-text list under each Education entry
  (Dean's List, President's List, Magna Cum Laude, Phi Beta Kappa, etc.)
- **Skill groups** with their original category headings (e.g. `"Security &
  Monitoring": [SIEM, Splunk, ...]`) — the categorisation is preserved in
  the exported resume

If your existing resume was uploaded before 2026-05-28 it may be missing
these fields (the schema didn't have a slot for them). To get them
populated for an existing resume, **delete the resume** from **Profiles →
Manage an existing profile → Delete a resume from a profile** and
**re-upload** the same PDF. The parser cache key is the PDF text hash, so
the delete is what triggers a fresh parse.

---

## 14. Analytics: Companies and Run History

### Companies

Horizontal bar chart of the top 20 companies by best overall match score, filtered by the sidebar minimum score. The table below shows per-company job count and best score per **active** track (ADR-071: only the profile's active tracks among Technical / Architecture / Leadership are shown).

### Run History

Shows total workflow runs and cumulative estimated API cost across all runs. The full runs table below includes per-run status, job counts, LLM call counts, and cost.

All of this is **scoped to the active profile** (ADR-062). The **System Dashboard**
(formerly Cost Dashboard) likewise defaults to the active profile; tick **All
profiles (system-wide)** there to see spend, security events, latency, and
reliability across every profile at once, and click a profile in the by-profile
breakdown to drill into it (ADR-073).

---

## 14a. Picking a Provider and Model per Agent

Open **Settings** → **Agent Models** to see the per-agent assignment. Each
configurable agent has a **Provider** dropdown (Claude / OpenAI) and a
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

## 15. Daily Workflow

Once configured, a typical session looks like:

```
1. (Optional) Add LinkedIn URLs to data/linkedin_inbox.txt (or paste into Custom job URLs on Start New Run)
2. Start backend + Streamlit UI if not running
3. Start New Run — fill form (optionally paste custom URLs), click Start Workflow
4. Switch to Live Run Monitor — refresh until run completes (~5–15 min)
   (Job selection is auto: every job whose best track score >= threshold
   advances to deep review — no HITL pause.)
5. Workflow History — click the row to open Workflow Detail
6. Read deep-reviewed jobs + interview prep on the detail screen
7. For jobs worth pursuing: scroll to Prep — tailored resume drafts,
   expand the job, click Generate new draft (5–15s per draft).
   Read the Strategy summary, scan the per-track Estimated impact,
   review section-grouped diffs, then Approve / Revise / Reject.
   Iterate by clicking Generate new draft again — drafts accumulate.
8. Switch to Run Report — read findings and download markdown
9. Browse Results for history across all runs
```

**Estimated cost per run (10 jobs):**

| Scenario | Estimated cost |
|---|---|
| Discovery + research + scoring only | ~$0.02–0.05 |
| Full run with deep review (auto-selected jobs) | ~$0.05–0.20 |
| Each on-demand tailoring draft (per job, post-run) | ~$0.01–0.02 |

---

## 16. Troubleshooting

**Backend starts in mock mode**
- Verify `.env` exists in the project root with `ANTHROPIC_API_KEY=sk-ant-...`
- Check: `python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(bool(os.getenv('ANTHROPIC_API_KEY')))"`

**No jobs discovered**
- Check `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`
- Verify `config/config.yaml` has entries in `search.titles` and `scrapers.adzuna.locations`

**Workflow seems to hang mid-run**
- The workflow runs end to end with no human-in-the-loop pause (ADR-059) — it does not stop and wait for input. A long run is usually research/scoring on many jobs or rate-limit backoff, not a HITL pause
- Open **Live Run Monitor**, click **Refresh**, and watch the activity feed. If a run truly stalls, you can request cancellation (ADR-083); it stops at the next node boundary
- Tailoring, deep review, interview prep, and the Resume Clinic happen on demand AFTER the run, not as in-run pauses

**Live Run Monitor shows "No active workflow"**
- The session state is in-browser only; it resets on page reload or if Streamlit restarts
- You can still use Browse views — all historical data is in `data/v2.db`

**Resume parse error or wrong resume being used**
- Confirm `resume.pdf` is in the project root (for the on-disk path), or upload via **Profiles → Add profile**
- Resumes are scoped per profile (ADR-062). To force a re-parse, open **Profiles → Manage an existing profile → Delete a resume from a profile**, pick the resume, tick the cascade-confirm checkbox, click Delete. Then re-upload via **Add a resume to a profile**. The parser cache is keyed by the PDF's text hash, so the delete is what forces a fresh parse under the latest parser prompt
- Confirm the right **Profile** is selected in the sidebar — the resume picker only lists the active profile's resumes

**No deep review results or interview prep data**
- These views require a workflow that completed a full deep review pass
- Check that status reached `completed` in **Live Run Monitor**

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
