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

## US State Extraction

`models/filters.py` also exports a utility function used across the entire pipeline:

### `extract_us_state(location: str | None) -> str | None`

Extracts a 2-letter US state abbreviation from an unstructured location string. Returns `None` if no US state can be identified.

**Strategy (tried in order):**
1. `, XX` pattern — matches common scraper formats like `"Atlanta, GA"` and `"Seattle, WA, United States"`
2. Full state name word-boundary match — handles `"Austin, Texas"` and `"New York, New York"`. Names are matched longest-first so `"west virginia"` is never shadowed by `"virginia"`.
3. Standalone 2-letter uppercase token at end of string — fallback for unusual formats.
4. **County/Parish/Borough lookup** — regex-matches `"[Name] County"`, `"[Name] Parish"`, or `"[Name] Borough"` substrings and looks up the base name in `_COUNTY_STATE` (~100 unambiguous entries). Handles the dominant Ladders format: `"Atlanta, Fulton County"` → `GA`, `"Seattle, King County"` → `WA`, `"Jersey City, Hudson County"` → `NJ`.
5. **City/borough lookup** — splits on commas and checks each segment against `_CITY_STATE` (~200 entries). Handles borough-as-location patterns: `"Grand Central, Manhattan"` → `NY`, `"Nob Hill, San Francisco"` → `CA`.

**Data structures:**
- `_COUNTY_STATE: dict[str, str]` — county base name (lower-case) → 2-letter state. Only includes unambiguous names; counties shared by 3+ states are omitted to avoid false positives.
- `_CITY_STATE: dict[str, str]` — city/borough name (lower-case) → 2-letter state. Covers all major US metros, tech hubs, and all five NYC boroughs.

**Examples:**
| Input | Output | Step |
|---|---|---|
| `"Atlanta, GA"` | `"GA"` | 1 |
| `"Austin, Texas"` | `"TX"` | 2 |
| `"Washington, DC"` | `"DC"` | 1 |
| `"New York, NY, United States"` | `"NY"` | 1 |
| `"Atlanta, Fulton County"` | `"GA"` | 4 |
| `"Seattle, King County"` | `"WA"` | 4 |
| `"Jersey City, Hudson County"` | `"NJ"` | 4 |
| `"Grand Central, Manhattan"` | `"NY"` | 5 |
| `"Nob Hill, San Francisco"` | `"CA"` | 5 |
| `"Remote"` | `None` | — |
| `"US"` | `None` | — |
| `None` | `None` | — |

This function is called by the `Job._fill_state` model validator, which means state extraction happens automatically when any scraper creates a `Job` object — no scraper-level code changes are required. `Database.backfill_states()` uses it to populate the `state` column for rows that pre-date this feature.

## How to Add or Remove a Filter

Edit `models/filters.py` only. Both the scraper and scoring agent will pick up the change automatically on next run. Extend the test cases in `tests/test_filters.py` with the new title or description to prevent future regressions.

## Tests

`tests/test_filters.py` covers:
- Every title seen in real noisy output is asserted to be excluded
- Every target role is asserted to pass
- Non-tech descriptions are asserted to fail the tech gate
- A runtime import check verifies both `adzuna.py` and `scoring_agent.py` import the **same object** from `models.filters` (not their own copy)
