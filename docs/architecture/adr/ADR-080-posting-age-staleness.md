# ADR-080: Posting-Age Staleness Signal + Opt-In Max-Age Filter

## Status

Accepted (2026-06-04). Implemented.

Extends ADR-064/065 (per-profile discovery filters) and ADR-079 (pre-scoring
filtering). Near-term patch for the Adzuna stale-listing problem; a root-cause
alternative (ATS-direct sources) is being explored separately in
`spike_job_data_sources.md`.

## Context

Live observation (2026-06-04): an Adzuna result renders fine, but its "Apply for
job" button leads to an employer listing that no longer exists. Verifying that
deadness directly is not viable:

- Adzuna **429-blocks** server-side fetches (`Retry-After: 3600`), so we cannot
  load the page to follow the apply link.
- The dead link is the **employer terminal** behind a JS/click-gated apply button —
  a plain HTTP probe never leaves Adzuna's 200 page.
- Many ATS pages **soft-expire** (200 + "no longer available"), so status code
  alone is insufficient even when reachable.

So automated link-verification is rejected (see the diagnostic in the 2026-06-04
session). The reliable, free proxy is **posting age**: a stale posting strongly
correlates with a pulled requisition, and Adzuna already returns the posted date
(`created`). The data is already modeled — `JobPosting.posted_at` is populated in
`JobDiscoveryService.normalize` from the v1 `Job.posted_at` (which the Adzuna
scraper parses from `created`). It is simply not persisted or surfaced today.

## Decision

Surface posting age and add an opt-in max-age filter. No network fetching; fully
deterministic; keeps the human as the decision-maker (a signal, not a tracker —
consistent with the filter-vs-tracker rule, ADR-057).

### A. Persist `posted_at`

Add a `posted_at TEXT` column to the `jobs` table (idempotent `ALTER TABLE ADD
COLUMN` migration in `init_db`, like prior columns). `JobRepository.upsert` writes
it; `discover_jobs` carries `posting.posted_at` into the `jobs` row and the state's
`normalized_jobs` entries so it flows to `scored_jobs` and the UI read path.

### B. Opt-in max-age filter (deterministic, ADR-065 shape)

New per-profile config `search.max_posting_age_days` (int; `0`/None = off). A
deterministic helper `app/services/posting_age_filter.py` computes age from
`posted_at`; `JobDiscoveryService.discover_with_stats` drops postings strictly
older than the cap, **right after the experience filter**. Conservative: a posting
with **no parseable `posted_at` is kept** (mirrors the experience filter's "silent
JD is not penalized"). The funnel `stats` gains `age_filter_dropped`. Off by
default, so Primary and existing runs are unchanged.

**Placement — upstream of the relevance filter (intentional).** Because it runs in
discovery, the age cap gates the input to *both* the ADR-079 relevance pre-filter
**and** scoring: a too-old posting is dropped before the relevance LLM ever reasons
over it and before any scoring spend. The age cut stays **deterministic** rather
than being folded into the relevance LLM's reasoning — age is a clean numeric
threshold that needs no model call, and keeping it in discovery means it also
benefits manual-selection and plain-auto runs (which never invoke the relevance
filter). The relevance filter stays focused on seniority/role fit; age is handled
for free, earlier, for every run.

### C. Surface age in the UI

The Job Detail read (`get_job_pipeline`, which reads `FROM jobs`) returns
`posted_at`. A pure formatter `format_posting_age()` (`app/ui/formatting.py`)
renders "Posted N days ago" plus a **stale badge** past a threshold (30 days,
reusing the v1 `is_stale` notion). Rendered on the Job Detail view — the screen the
user opens to confirm a score. The Start New Run form gains a
`search.max_posting_age_days` number input (0 = off), persisted as a profile
default like the ADR-065 knobs.

## Options considered

- **Automated link-verification (HTTP probe).** Rejected: Adzuna 429-blocks us, the
  dead link is JS/click-gated past the Adzuna page, and soft-expiry returns 200.
- **Headless-browser (Playwright) probe.** Rejected for the run path: heavy, slow,
  brittle, may still be blocked.
- **Posting-age proxy (chosen).** Free, deterministic, already-modeled data; a
  reliable correlate of staleness without any fetch.
- **Switch off Adzuna entirely.** Out of scope here; tracked as the separate ATS-
  direct spike (`spike_job_data_sources.md`).

## Consequences

### Positive

- The user sees how fresh a scored posting is and can opt to auto-drop old noise —
  the dead-link pain is mitigated with zero network cost.
- Reuses the ADR-065 filter shape and the existing migration + read seams.

### Tradeoffs

- Age is a proxy, not proof: a fresh posting can still be dead, and a genuinely
  open older role can be dropped if the user sets an aggressive cap (hence opt-in,
  and keep-when-unknown).
- `posted_at` quality depends on the source populating it; sources that omit it
  yield no age signal (kept, by design).

### Neutral

- Docs (per the architecture-docs sweep mandate): ADR-080 + index, `data_model.md`
  (`jobs.posted_at`), `config_model.md` (`search.max_posting_age_days`),
  `workflow_model.md` (discovery funnel), `architecture_overview.md`, `wiki.md`,
  CLAUDE.md scraper rules, `config.example.yaml`, CHANGELOG. Tests: age helper,
  discover_with_stats age-drop + keep-when-unknown, formatter, upsert persistence.

## References

- ADR-065 — Experience-targeted discovery (the deterministic filter shape reused).
- ADR-079 — Relevance pre-filter (the prior pre-scoring filter).
- ADR-057 — Filter-vs-tracker distinction (a staleness signal is a filter, not
  application tracking).
- `spike_job_data_sources.md` — the parallel root-cause exploration (ATS-direct
  sources).
