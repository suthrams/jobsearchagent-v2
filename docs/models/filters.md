# models/filters.py — Shared Filter Lists

## Purpose

Centralised filter lists used by **two independent gatekeeping layers**:

1. `scrapers/adzuna.py` — drops irrelevant titles *before* storing jobs (saves DB writes and reduces noise ingested)
2. `agents/scoring_agent.py` — drops irrelevant titles and non-tech descriptions *before* calling Claude (saves API cost)

Keeping both lists in one file prevents drift. Previously each file maintained its own copy; when the scraper's list was updated the scoring agent's was not, causing LinkedIn jobs (which bypass the Adzuna scraper) to reach Claude with the old, shorter exclusion list.

## Why Two Layers?

| Layer | Where | Catches |
|---|---|---|
| Scraper (Adzuna) | `_is_relevant_title()` | Noise returned by Adzuna API — drops before DB insert |
| Scoring Agent | `_is_excluded_title()` + `_has_tech_description()` | All sources including LinkedIn — drops before Claude call |

The scraper filter is an early optimisation. The scoring agent filter is the authoritative cost gate.

## Filter Lists

### `RELEVANT_TITLE_KEYWORDS`
*Used only by the Adzuna scraper.* Title must contain at least one to pass the inclusion check:

`engineer`, `architect`, `director`, `manager`, `principal`, `staff`, `lead`, `head of`, `vp`, `vice president`, `software`, `platform`, `cloud`, `devops`, `sre`, `infrastructure`, `solutions`, `developer`, `iot`, `embedded`, `connected devices`, `edge computing`

### `EXCLUDED_TITLE_KEYWORDS`
*Used by both scraper and scoring agent.* Title must NOT contain any of these. Categories:

| Category | Examples |
|---|---|
| Sales / BD | `presales`, `sales manager`, `sales engineer`, `account manager`, `business development` |
| Non-tech management | `property manager`, `community manager`, `leasing`, `project manager`, `program manager`, `office manager`, `operations manager`, `fundraising`, `transcription` |
| Non-software engineering | `electrical engineer`, `civil engineer`, `structural engineer`, `landscape architect`, `design specification`, `hvac`, `substation` |
| Hospitality / non-IT | `hotel`, `hvac`, `medical` |
| Junior / student | `intern`, `internship`, `associate engineer` |
| Language-specific | `java developer`, `java engineer` |

### `TECH_DESCRIPTION_KEYWORDS`
*Used only by the scoring agent.* At least one must appear in the job description. Deliberately excludes broad terms (`"technology"`, `"technical"`, `" it "`, `"information technology"`) that match HR, biotech, and office management descriptions:

| Category | Keywords |
|---|---|
| Languages | `software`, `python`, `javascript`, `typescript`, `.net`, `golang`, `rust`, `java` |
| Cloud / infra | `cloud`, `aws`, `azure`, `gcp`, `kubernetes`, `docker`, `terraform`, `ci/cd`, `cicd`, `infrastructure`, `devops`, `platform engineering` |
| Architecture | `api`, `microservice`, `distributed system`, `backend`, `frontend`, `full stack`, `saas`, `paas`, `application development` |
| Data / AI | `data engineering`, `data pipeline`, `machine learning`, `artificial intelligence`, ` ai `, `llm`, `database` |
| Leadership (scoped) | `engineering team`, `software engineer`, `software development`, `digital transformation` |
| IoT / edge | `iot`, `internet of things`, `mqtt`, `edge computing`, `embedded`, `connected devices`, `iiot`, `device management`, `telemetry`, `firmware` |

## How to Add or Remove a Filter

Edit `models/filters.py` only. Both the scraper and scoring agent will pick up the change automatically on next run. Extend the test cases in `tests/test_filters.py` with the new title or description to prevent future regressions.

## Tests

`tests/test_filters.py` covers:
- Every title seen in real noisy output is asserted to be excluded
- Every target role is asserted to pass
- Non-tech descriptions are asserted to fail the tech gate
- A runtime import check verifies both `adzuna.py` and `scoring_agent.py` import the **same object** from `models.filters` (not their own copy)
