# ADR-064: Per-Profile Search Criteria Drive Discovery; Configurable Relevance Filters

## Status

Accepted (2026-05-26). Implemented.

Implementation notes (where the build refined this draft):
- **Relevance is derived from the run's role tokens** (`relevance_tokens()`), passed
  to the per-run Adzuna scraper; the senior keyword lists remain the default for
  no-roles runs. Making `relevance_keywords` / `excluded_keywords` *explicit*
  per-profile config keys (Decision B) is deferred — the role-derived default
  covers the motivating case without new config surface.
- **Locations are entered one-per-line** in the UI (Start New Run + onboarding),
  not comma-separated, so "City, State" is not shattered into two entries. A
  pre-existing mangled `search.locations` for profile 0 was repaired.
- **Primary's criteria were already tied to profile 0** via `user_config`
  (`search.titles` / `search.locations`); no `init_db` seed was added (a fresh DB
  inherits the YAML `search` defaults). The repair above corrected the locations.
- "Remote" appearing in a run's locations triggers the remote (no-location) Adzuna
  search with the roles as keywords; other entries are treated as physical locations.

Builds on ADR-062 (multi-user profiles: per-user `search` defaults), ADR-061
(configurable funnel width), ADR-060 (manual scoring selection), ADR-058 (move
policy data from code to `config.yaml`), and ADR-050 (wrap the v1 Adzuna scraper).

## Context

ADR-062 lets each profile save its own `search` defaults (roles, locations) and
pre-fills the Start New Run form from them. The intent was that a second profile —
for example an entry-level cybersecurity new-grad, distinct from the senior
IC/architect/management owner of profile 0 — could run searches tuned to its own
target roles.

Reconnaissance of the discovery path shows that intent is **not** met today:

1. **The run's search criteria do not drive auto-discovery.**
   `discover_jobs` passes `state["search_criteria"]` into
   `JobDiscoveryService.discover(workflow_id, search_criteria, ...)`, but
   `discover()` never reads it. The built-in scrapers are called as
   `scraper.scrape()` with no arguments; the Adzuna scraper searches `self.titles`,
   which is `config.yaml search.titles` captured **once at backend startup** from
   user 0's effective config. The Adzuna scraper is a startup singleton; switching
   profiles in the UI does not rebuild it (per-profile runtime swap is deferred to
   "Phase 9"). So roles/locations a profile sets are recorded and pre-fill the form
   but change neither the Adzuna query nor anything downstream — they are
   effectively vestigial for discovery, and `search_criteria` is not fed to scoring
   either.

2. **The title-relevance gate is senior-hardcoded.** `models/filters.py`
   `RELEVANT_TITLE_KEYWORDS` (engineer, architect, director, principal, staff,
   lead, ...) is applied inside the v1 Adzuna scraper; it does **not** contain
   `analyst`, `security`, `cyber`, or `soc`, so most entry-level cybersecurity
   titles are dropped from Adzuna results regardless of configuration.
   `EXCLUDED_TITLE_KEYWORDS` additionally drops `intern` / `internship` /
   `associate engineer`, which a new-grad may legitimately want.

3. **Scoring is senior-tuned.** The three tracks (`ic`, `architect`,
   `management`) and their prompts assume senior experience; the default
   `scoring.min_match_score` is 75. An entry-level resume scores modestly and
   typically falls below threshold ("no qualifying jobs").

The only path that works for a non-senior persona today is **custom job URLs**
(per-run `CustomUrlScraper`), which bypass the senior relevance gate (only the
exclusion list applies) and are scored/reviewed/tailored normally — combined with
lowering `scoring.min_match_score` (which *is* honored per profile/run). That is a
viable manual workaround but does not deliver the per-profile auto-discovery that
ADR-062 implied.

This ADR closes the gap for discovery and the relevance filter. Persona-aware
**scoring** is explicitly out of scope (Decision C).

## Decision

### A. Discovery honors the run's `search_criteria`

Build the Adzuna query from the run's `search_criteria` (roles + locations) when
present, falling back to the startup `config.yaml` configuration when absent
(today's behavior, fully backward compatible).

Preferred implementation — a **per-run Adzuna scraper**, mirroring the existing
per-run `CustomUrlScraper` pattern:

- Add an `adzuna_scraper_factory(search_criteria) -> scraper | None` to
  `WorkflowDependencies`. `discover_jobs` builds a per-run Adzuna scraper from the
  run's roles + locations (combined with the static `scrapers.adzuna` settings:
  country, radius, results_per_page, remote_keywords) and passes it as an extra
  scraper.
- When a per-run Adzuna scraper is used, the startup built-in Adzuna is **skipped
  for that run** so the senior startup titles are not also searched. Runs with no
  roles use the built-in (unchanged).
- Because use is sequential (ADR-062), a per-run build is cheap and avoids mutating
  any shared singleton.

This also requires reconciling a naming drift: the Start New Run form and
`search_criteria` use `roles`; ADR-062 onboarding stores `search.titles`. The two
are synonyms at the discovery boundary; the implementation standardizes on one
(recommend `search.roles`) and keeps an alias read for the other.

### B. Relevance and exclusion keyword lists become per-profile config

Move the keyword policy out of hardcode and into the layered config (the same
move ADR-058 made for the model catalog):

- New `search.relevance_keywords` and `search.excluded_keywords`, defaulting in
  `config.yaml` to today's senior/exclusion lists so existing behavior is
  unchanged for profile 0.
- The relevance gate moves from inside the v1 Adzuna scraper into
  `JobDiscoveryService` (v2), which already owns `_is_excluded_title`. Both lists
  are read from the **run's effective config**, so a profile can override them
  (the cyber grad sets relevance to `security`, `analyst`, `soc`, `cyber`,
  `information security` and clears the junior/student exclusions).
- **When explicit roles drive the search, relevance defaults to "title contains
  any searched role token"** — so a profile that sets roles does not also have to
  curate a relevance list; the search terms are the relevance signal. The
  `excluded_keywords` list still applies on top.

### C. Scoring rubric stays senior-tuned (acknowledged limitation, deferred)

The three tracks and their prompts are unchanged. Entry-level scores will be
modest by design; the lever is the already-honored per-profile
`scoring.min_match_score`. A persona-aware scoring rubric (e.g., an "entry-level"
track or per-profile rubric selection) is a larger prompt-engineering change and
is explicitly out of scope here. The UI/docs must state plainly that scores are
calibrated for senior roles so a user does not misread a low entry-level score.

### D. Backward compatibility and sequential use

A run with no roles behaves exactly as today (built-in Adzuna, senior defaults).
Per-run scraper construction is consistent with the existing per-run
`CustomUrlScraper` and introduces no global-singleton mutation. The Adzuna quota
guard still applies, now computed from the per-run title count (see Consequences).

## Options considered

- **Per-run Adzuna scraper built from `search_criteria` (chosen).** Consistent
  with the existing per-run `CustomUrlScraper` factory; no shared-state mutation;
  naturally per-profile under sequential use.
- **Mutate the startup scraper's `titles` per call.** Rejected — mutating a shared
  singleton is fragile and only safe by accident under sequential use.
- **Rebuild all deps/scrapers on profile switch.** Rejected for now — that is the
  deferred "Phase 9" per-profile runtime swap; heavier than needed when the run
  already carries the criteria.
- **Relevance: keep hardcoded but broaden the list.** Rejected — it would bias the
  shared list toward whatever personas we happen to add and still not be
  per-profile. Config-driven + derive-from-roles generalizes cleanly.
- **Do nothing (custom-URL workaround only).** Rejected — it leaves the
  per-profile promise of ADR-062 hollow for auto-discovery.

## Consequences

### Positive

- ADR-062 profiles become genuinely useful for different personas; a new-grad
  cybersecurity profile (and any non-senior search) gets real auto-discovery.
- Roles/locations stop being vestigial — what you enter is what gets searched.
- The senior bias becomes a *default*, not a hardcode, and is overridable per
  profile.

### Tradeoffs

- **Adzuna quota** is now driven by the per-run title count. The existing guard
  (`len(titles) x len(locations) + len(remote_keywords) < ~100/day`) must be
  enforced per run and surfaced if a profile's criteria would blow the budget.
- Moving the relevance gate into `JobDiscoveryService` means Adzuna returns more
  rows that v2 then filters; negligible at current volumes but worth a note.
- **Scoring remains senior-biased** — this ADR is a partial solution. Without
  Decision C follow-up, entry-level runs lean on custom thresholds and the per-job
  review/tailoring value rather than high overall scores. Must be documented to
  avoid surprise.

### Neutral

- Docs to update: `config_model.md` (new `search.roles` / `relevance_keywords` /
  `excluded_keywords` keys), `workflow_model.md` (discovery now consumes
  `search_criteria`), `CLAUDE.md` scraper rules (per-run Adzuna build; relevance in
  v2), `user_guide.md` (per-profile search now drives discovery; scoring caveat).
- Tests: discovery honors run roles/locations; falls back to config when absent;
  relevance/exclusion read from effective config; derive-from-roles default;
  quota guard on per-run criteria.

## References

- ADR-062 — Multi-user profiles (per-profile `search` defaults this ADR makes
  load-bearing for discovery).
- ADR-058 — Move policy data (model catalog) from code to `config.yaml`; this ADR
  applies the same move to relevance/exclusion keyword lists.
- ADR-050 — Wrap the v1 Adzuna scraper (the scraper a per-run build instantiates).
- ADR-061 / ADR-060 — Funnel width and manual scoring selection (the per-run
  configuration surface this extends).
