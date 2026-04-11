# prompts/ — Prompt Templates Overview

## Purpose

All Claude prompt text lives here as Markdown files with XML-tagged sections. Keeping prompts out of Python code follows the **Prompt-as-Template** agentic pattern — it separates prompt engineering from application logic and makes prompts easy to iterate on without touching code.

## Template Format

Templates use `{{double_brace}}` variable placeholders, loaded and substituted by `claude/prompt_loader.py`. XML tags (e.g., `<profile>`, `<jobs>`) provide structure that Claude can navigate reliably.

---

## parse_resume.md

**Used by:** `ProfileAgent`  
**Variables:** `{{resume_text}}`  
**Output:** JSON object matching `Profile` schema

Extracts structured profile data from raw resume text. Instructs Claude to return only JSON — no preamble, no markdown.

**Fields extracted:**
- `name`, `headline`, `email`, `location`, `summary`
- `experience[]` — each role: company, title, start_year, end_year, description, technologies
- `skills[]` — flat list of all technologies mentioned
- `education[]` — institution, degree, year
- `certifications[]` — name, issuer, year

**Prompt design notes:**
- The instruction "if headline is not present, derive one from their most recent title" ensures the field is always populated — reduces null-handling downstream.
- Temperature 0.1 ensures consistent extraction — same resume always produces the same JSON.

---

## score_job.md

**Used by:** `ScoringAgent`  
**Variables:** `{{profile}}`, `{{tracks}}`, `{{salary_min}}`, `{{salary_currency}}`  
**Output:** JSON array of `BatchJobScore` objects

Scores up to 10 jobs in a single call. Each job in the input is wrapped in `<job index="N">` tags so Claude can return results in any order and they can be remapped by index.

`num_jobs` is intentionally **not** a template variable. It was removed because including it in the cached system prompt caused a cache miss on the last batch of every run (when the count differs from `BATCH_SIZE`). The job count is passed in the user message instead, keeping the system prompt byte-identical across all batches.

**Key instructions given to Claude:**
- Score range meaning: 80–100 excellent, 60–79 good, 40–59 partial, 0–39 poor
- `recommended = true` when score >= 65
- Deduct 10 points if salary is below `{{salary_min}} {{salary_currency}}` and note it in the summary
- Note stale postings (>30 days) in summaries for recommended tracks
- Only score tracks listed in `<tracks/>` — set all others to `null`

**Output format:**
```json
[
  {
    "job_index": 0,
    "ic": {"score": 82, "summary": "...", "recommended": true},
    "architect": {"score": 75, "summary": "...", "recommended": true},
    "management": null
  }
]
```

**Prompt design notes:**
- The `job_index` field is critical — it allows the agent to correctly map scores back to jobs even if Claude returns them out of order.
- Salary penalty and staleness note are injected as instructions rather than hard-coded — they're configurable via `config.yaml`.

---

## tailor_resume.md

**Used by:** `TailoringAgent`  
**Variables:** `{{profile}}`, `{{job}}`, `{{track}}`  
**Output:** JSON object with tailored resume sections

Rewrites your resume for a specific job and career track. Higher temperature (0.3) than scoring prompts to produce more natural language.

**Fields produced:**
- `tailored_summary` — 3–4 sentence professional summary opening with title + years, highlighting relevant skills, written in first person
- `highlighted_experience[]` — per-role bullets selected and rewritten for relevance; empty array if the role has no relevant content
- `keywords[]` — ATS keywords from the job that match your background
- `gaps[]` — requirements you don't clearly meet (honest, actionable)

**Prompt design notes:**
- "Is written in first person, present tense" and "Does not mention the company name" are explicit constraints to keep the output portable.
- Separating `keywords` and `gaps` as distinct fields makes them easy to display separately in the terminal output and `.txt` file.

---

## Prompt Files vs. Code

| Approach | Example |
|---|---|
| Hardcoded string | `f"You are a scorer. Profile: {profile}. Job: {job}"` |
| Prompt-as-Template | `prompts/score_job.md` loaded by `PromptLoader` |

The template approach wins in practice because:
- You can open a `.md` file in any editor and read the prompt as a document
- Git diffs on prompt changes are readable
- Adding a new variable only requires adding `{{var}}` to the file and passing it in `loader.load()`
- The same file can be shared with a non-developer who wants to tune the scoring criteria
