# ADR-063: Retire the v1 Reference Code (Keep the Shared Scraper/Model Libraries)

## Status

Accepted (2026-05-26).

Supersedes the "keep v1 stable for reference" half of **ADR-001** (Keep v1 Stable
and Use v2 for Refactor). ADR-001's other decisions (use v2 as the refactor
branch, adopt LangGraph/LangChain) stand; only the indefinite retention of the v1
runtime as parallel reference code is reversed here.

## Context

ADR-001 kept the v1 implementation (`main.py`, `dashboard.py`, `agents/`,
`storage/`, `claude/`, `prompts/`, and the v1 `models/` + `scrapers/`) in the tree
as a stable reference while v2 was built ground-up under `app/`. v2 is now the
system in use: phases 1-8 plus a long tail of post-8 work (ADR-053 through
ADR-062) are complete, the FastAPI + Streamlit + LangGraph stack is the only path
exercised, and the test suite covers v2.

The v1 runtime has not been run in months and is no longer a useful reference:
its patterns have either been superseded (v2 has its own agents, providers,
repositories, prompts) or documented in the ADRs and architecture docs. Keeping
it carries cost — it is dead code that confuses navigation, inflates the surface
a reader has to reason about, and pins a `tests/test_db.py` / `tests/test_filters.py`
pair to modules nothing else touches.

Reconnaissance of the import graph shows v2 does **not** depend on the v1 runtime,
with one nuance: a small set of v1 *libraries* are imported by v2 and must be
kept.

- v2 imports, directly or transitively:
  - `scrapers/adzuna.py` (wrapped by `app/services/concurrent_adzuna_scraper.py`),
    `scrapers/linkedin.py` (built in `app/api/dependencies.py`), and their
    `scrapers/base.py`.
  - `models/config_schema.py` (`AdzunaConfig`), `models/filters.py`
    (`EXCLUDED_TITLE_KEYWORDS`, `TECH_DESCRIPTION_KEYWORDS`, `RELEVANT_TITLE_KEYWORDS`),
    and `models/job.py` (`Job`, `JobSource`, `SalaryRange`, used by the scrapers).
- v2 does **not** import: `main.py`, `dashboard.py`, `agents/`, `storage/`,
  `claude/`, `prompts/`, `scrapers/ladders.py`, or `models/profile.py`.

## Decision

Delete the v1 runtime that v2 does not import; keep the v1 modules that it does,
reframing them as shared libraries rather than "v1 reference."

**Removed**

- `main.py`, `dashboard.py` — v1 entry points.
- `agents/` — v1 agents (v2 has `app/agents/`).
- `storage/` — v1 persistence (v2 has `app/repositories/`).
- `claude/` — v1 LLM client (v2 has `app/providers/`).
- `prompts/` — v1 prompts (v2 has `app/prompts/`).
- `scrapers/ladders.py` — Ladders scraper, never wired into v2.
- `models/profile.py` — v1 resume profile (v2 has `app/schemas/resume_profile.py`).
- `tests/test_db.py` (exercised `storage/`) and `tests/test_filters.py` (exercised
  `agents/scoring_agent.py`). `tests/test_adzuna_scraper.py` is retained — it
  covers a kept library.

**Kept (now shared libraries, not "v1 reference")**

- `scrapers/{__init__,base,adzuna,linkedin}.py`
- `models/{__init__,config_schema,filters,job}.py`

The two package `__init__.py` files drop their imports of the removed siblings
(`LaddersScraper`, `Profile`).

## Options considered

- **Delete only the v1 modules v2 does not import (chosen).** Smallest safe cut:
  removes dead code while preserving the Adzuna/LinkedIn scraping path and the
  shared filters/job schema that v2 genuinely relies on.
- **Delete all of v1, including the imported scrapers/models.** Rejected — it
  would break job discovery and the filter gate. v2 would have to re-home those
  modules under `app/` first, which is a larger refactor not justified now.
- **Keep v1 indefinitely (status quo, ADR-001).** Rejected — the reference value
  has lapsed; the cost is ongoing reader confusion and orphaned tests.

## Consequences

### Positive

- Smaller, clearer tree: the only runtime is v2 under `app/`, plus a clearly
  labelled set of shared scraper/model libraries.
- The test suite no longer pins modules nothing else uses.
- New contributors are not misled into reading or "fixing" dead v1 code.

### Tradeoffs

- The v1 runtime is no longer available as a side-by-side reference. It remains
  fully recoverable from git history (this commit's parent) if ever needed.
- `scrapers/` and `models/` now mix "shared library used by v2" with their v1
  lineage; their `__init__.py` headers and the CLAUDE.md note document this so a
  reader does not mistake them for live v1.

### Neutral

- CLAUDE.md's "v1 Reference (do not modify)" section is replaced with a
  "Shared libraries from v1" note; the doc index (`docs/wiki.md`,
  `docs/README.md`) drops pointers to removed v1 doc pages.

## References

- ADR-001 — Keep v1 Stable and Use v2 for Refactor (the retention decision this
  reverses).
- ADR-050 — Wrap v1 AdzunaScraper with a concurrent adapter (why `scrapers/adzuna.py`
  is kept).
