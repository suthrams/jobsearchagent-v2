# Settings Reference — Job Search Agent v2

Every configuration setting, what it is **for**, and **how it changes what a run
does** (cost, breadth, strictness, results). Operator-facing companion to
[business_rules.md](business_rules.md) (the rules these settings drive).

**No values by design.** This reference describes *purpose and effect*, not current
numbers. Defaults, ranges, and ceilings live in
[`config/config.example.yaml`](../config/config.example.yaml) (the documented
template) and [`docs/architecture/config_model.md`](architecture/config_model.md)
(defaults/ceiling table). Open those for the current value of any setting.

## How settings are layered and changed

- **Two layers (ADR-062):** `config/config.yaml` holds system **defaults**;
  per-profile **overrides** layer on top, stored in the `user_config` table. A new
  profile runs on pure defaults until it sets its own. Read via
  `ConfigService.get_effective_config(user_id)`.
- **Where you change them:** most `search.*` / `scoring.*` / `agents.*` settings are
  meant to be set **per profile in the Settings UI** (writes `user_config`) or in
  the **Start New Run** form for that one run. `config.yaml` is for the
  install-wide defaults and the system-only blocks (`retention.*`, `models.*`).
- **When changes take effect:** `search.*` / `scoring.*` apply on the **next run**;
  per-agent model/provider changes apply on **restart** (ADR-053).
- **Protected settings** (models, hard limits, retention, cost-gating thresholds)
  cannot be overridden per profile — they are silently ignored if attempted
  (`_PROTECTED_KEYS`, ADR-062). They change only in `config.yaml`.

---

## 1. Search & discovery (`search.*`)

These shape **which postings enter the funnel** — the widest, cheapest end. Tighter
search = fewer jobs reach paid scoring = lower cost and less noise.

| Setting | Purpose | Effect on processing |
|---|---|---|
| `search.titles` | Keyword set that drives Adzuna scraping; also pre-fills the Start New Run form. | More/broader titles = a wider pool and more API quota used; narrow, senior titles = fewer but more relevant postings. Quota guard: titles x locations + remote keywords must stay under the daily Adzuna cap. |
| `search.locations` | Locations to search; pre-fills the run form. One per line; "Remote" triggers the remote search. (ADR-064) | Each location is a separate scrape — more locations = more quota and a larger pool. |
| `search.max_years_experience` | Drop postings asking for **more** than N years (opt-in; 0/absent = off). (ADR-065) | Filters out roles above your target seniority *before* scoring — cuts noise and cost for an early-career profile. |
| `search.min_years_experience` | Drop postings asking for **fewer** than N years (opt-in; 0/absent = off). (ADR-065) | Filters out junior roles for a senior profile. |
| `search.exclude_senior` | Drop senior/principal/staff/lead/director titles (opt-in). (ADR-065) | A coarse seniority cut at discovery; useful when titles pull in too-senior roles. |
| `search.relevance_filter` | Turn on the one-pass reasoning pre-filter that drops clear seniority/role mismatches **before** scoring (opt-in). (ADR-079) | **Cost-negative on a noisy pool:** one cheap LLM call removes mismatches so you don't pay the per-job scoring on them. Profile-relative + bidirectional. Never drops everything on failure (keeps all jobs). Widens discovery to the wide-net cap. |
| `search.max_posting_age_days` | Drop postings older than N days at discovery (opt-in; 0/absent = off). (ADR-080) | Removes stale requisitions (which correlate with dead apply links) cheaply, with no network fetch. Postings with no parseable date are kept. |
| `search.max_discovered` | Width of the wide-net discovery pool (only meaningful in manual-selection / relevance-filter modes). (ADR-060/061) | Larger net = more candidates to filter/select from, up to the system ceiling; ignored in plain auto mode. |

---

## 2. Scoring & selection (`scoring.*`)

These govern **how jobs are judged and which ones earn expensive downstream work**
(deep review, advice, interview prep). This is the main cost/strictness dial.

| Setting | Purpose | Effect on processing |
|---|---|---|
| `scoring.tracks` | The subset of the three career tracks the profile pursues: `ic` (technical), `architect` (architecture), `management` (leadership). Default = all three. (ADR-071) | Inactive tracks are **not scored**, do not gate selection, and are hidden in the UI. Narrowing tracks focuses scoring on the dimensions you care about and can change which jobs qualify. |
| `scoring.career_track` | Weighting *emphasis* among the active tracks (`ic`/`architect`/`management`/`all`). Distinct from `tracks` (inclusion). | Tilts `overall_score` and commentary toward the named track; `all` weights active tracks equally. Does not exclude any active track. |
| `scoring.min_match_score` | The qualification threshold: a job qualifies for deep review if **any active track score meets it**. (ADR-071) | **The strictness/cost lever.** Higher = fewer jobs reach deep review + career advice (cheaper, stricter); lower = more marginal jobs pull in the expensive agents. Note it is per-track, so a single strong track can qualify a job whose overall is lower. |
| `scoring.max_scored` | How many jobs get research + scoring per run (override of the default cap, up to the system ceiling). (ADR-061) | The scored-funnel width. Lower = fewer research+scoring calls = lower cost, at the risk of missing matches; in auto mode it also bounds discovery. |
| `scoring.manual_selection` | Park the run after discovery so you pick which jobs to score, instead of auto-scoring. (ADR-060) | Two-phase run (curate-before-scoring): discovery casts the wide net, you choose, then phase 2 scores only your picks. Maximum control, no wasted scoring spend. |
| `scoring.auto_interview_prep` | Auto-run the in-graph interview coach (default **off**). (ADR-085) | **Off (default):** interview prep is on-demand only (`POST .../interview-prep`) — saves a premium-model call on every run. **On:** the coach fires automatically when a selected job clears `min_match_score`. |

---

## 3. Job sources (`scrapers.*`)

Which feeds discovery pulls from. Adzuna is the broad aggregator; Greenhouse/Lever/Workday
are source-of-truth employer feeds (ADR-081/101). The ATS company list is **per-profile**
and **managed from the Settings UI** ("Target companies" section): pick an ATS, enter
a board token/slug (or, for Workday, **paste the career URL**), and it is **verified
live before it joins your list** (a dead board is rejected). A brand-new profile
**inherits the operator-set default batch** (ADR-097; Workday ships **off** with an
empty list); saving your own list **replaces** the default for that ATS. Because the
list resolves per run from your `effective_config`, an edit applies on your **next run
with no restart/reload** (ADR-098).

| Setting | Purpose | Effect on processing |
|---|---|---|
| `scrapers.adzuna.enabled` | Toggle the Adzuna aggregator. | Off = no Adzuna postings; the broad pool disappears. |
| `scrapers.adzuna.country` | Adzuna country code. | Scopes results to that market. |
| `scrapers.adzuna.locations` | Locations Adzuna queries. | Each is a separate call (quota); more = wider pool. |
| `scrapers.adzuna.radius_km` | Search radius around each location. | Wider radius = more postings, less geographic precision. |
| `scrapers.adzuna.results_per_page` | Results pulled per Adzuna call. | Higher = more postings per call (more quota/cost per scrape); bounded by the free-tier max. |
| `scrapers.adzuna.remote_keywords` | Keywords used for the no-location "remote" search. | Each adds a remote scrape; counts against the daily quota guard. |
| `scrapers.adzuna.max_calls_per_minute` | Client-side per-minute rate limiter on Adzuna calls (ADR-107). Default 20; `0` disables. | Paces call starts so a burst of concurrent scrapes stays under Adzuna's per-minute hits cap (the "20/25 hits per minute" alert). Lower = safer but slower discovery; process-global, so concurrent runs share one budget. |
| `scrapers.adzuna.max_calls_per_run` | Cap on Adzuna calls per run (one per title x location + remote), ADR-108. Default 50; `0` uncaps. | Stops an unbounded role/location grid (e.g. 19 roles x 10 locations = ~209 calls) from blowing the per-minute/daily quotas or timing out discovery. The kept subset is sampled by a diagonal interleave (broad title + location coverage) and **rotated per run**, so combinations beyond the cap are deferred to a later run (not dropped permanently); a timed-out scrape returns partial results, never zero. Raise for more coverage per run (slower runs, more quota). |
| `scrapers.greenhouse.enabled` / `.companies` | ATS-direct Greenhouse feeds, queried **per company** (board tokens; empty = off). Per-profile + UI-managed with verify-on-add (ADR-081/097/098). | Adds live, source-of-truth listings with real employer apply URLs (no dead-link/429 issue). Only the listed companies are queried; title relevance uses the run's roles. |
| `scrapers.lever.enabled` / `.companies` | ATS-direct Lever feeds, queried per company (slugs; empty = off). Per-profile + UI-managed with verify-on-add (ADR-081/097/098). | Same as Greenhouse, for Lever-hosted boards. |
| `scrapers.workday.enabled` / `.companies` | ATS-direct Workday feeds, queried per company. `companies` is a list of structured `{tenant, dc, site}` triples (added by pasting the career URL; empty = off). Per-profile + UI-managed with verify-on-add (ADR-101). Ships off by default. | Returns the **full job description** (not Adzuna's ~500-char snippet), so the clearance/experience filters see the real requirement text; employer-direct apply URLs. Volume is bounded by a list-then-title-filter-then-capped-detail fetch. |

---

## 4. Per-agent model assignment (`agents.<name>.{provider, model}`)

Each agent's `(provider, model)` — the core **cost/quality** dial per agent
(ADR-053/058). High-volume agents default to the cheapest capable model; nuanced
advisory/generation agents default to the premium tier.

- **Effect on processing:** assigning a stronger model raises that agent's quality
  and its per-call cost; a cheaper model does the reverse. Because some agents run
  many times per run (research, scoring, critic, auditor), their model choice
  dominates run cost — which is why they default to the cheap tier.
- **Cost-cap guardrail (C2):** the cost-capped high-volume agents (research,
  scoring) **cannot** be assigned a model outside the `HIGH_VOLUME_SAFE_MODELS`
  allowlist in `app/providers/model_registry.py`. The override is rejected and a
  `cost_cap_violation` event is recorded. That allowlist is a **policy boundary in
  code, not configuration.**
- **Validation:** an assigned `(provider, model)` must exist in the catalog
  (Section 5). The per-agent pins are guarded by `tests/model_pins.json` (ADR-058).
- **Applies on restart**, not mid-session (ADR-053).

The agents you can assign: `research_agent`, `scoring_agent`, `relevance_filter`,
`resume_critic`, `review_auditor`, `fidelity_reviewer`, `career_advisor`,
`interview_coach`, `tailoring_agent`, `resume_parser`, `custom_url_extractor`,
`resume_reviewer`, `resume_chat`. See [agent_model.md](architecture/agent_model.md)
for each agent's role and [model_recommendations.md](model_recommendations.md) for
the recommended assignment + escalation order.

---

## 5. Model catalog & pricing (`models.providers.*`)

The registered `(provider, model)` pairs and their per-million-token input/output
prices (ADR-058).

- **Purpose:** this block is how the system **learns about new models or new
  prices** — edit it, no code release needed.
- **Effect on processing:** the prices here drive every cost figure the app reports
  (the `llm_calls.estimated_cost` ledger, the Cost dashboard, reconciliation). If a
  price here is stale, the app's cost numbers drift from the provider's bill.
- **Validation:** `ConfigService` validates this block on load; any agent
  assignment (Section 4) must reference a pair listed here. Adding a new *provider*
  (not just a model) also needs a provider implementation in code.
- **System-only:** edited in `config.yaml`, not per profile.

---

## 6. Data retention (`retention.*`)

Windows for the explicit purge (ADR-070). **All retention is manual** — the purge
never runs automatically; fire it via `POST /admin/purge`, `tools/purge_data.py`,
or the Settings control.

| Setting | Purpose | Effect on processing |
|---|---|---|
| `retention.workflow_runs_days` | Age after which a run is eligible for purge. | Purging a run **cascades to all its child rows** (jobs, scores, reviews, advice, tailorings). |
| `retention.observability_days` | Window for observability tables (llm_calls, agent_events, step_executions, api_requests). | Shorter = leaner telemetry history; the Cost/System dashboards see less back-history. |
| `retention.security_events_days` | Window for `security_events`. | Controls how far back the security view reaches. |
| `retention.memory_items_days` | Window for `memory_items` (designed, not yet wired). | No runtime effect today; future memory retention. |
| `retention.jobs_days` | Window for discovered/scored jobs not tied to a surviving run. | Trims orphaned job rows. |
| `retention.resumes_days` | Window for resumes. | A resume is purged only when inactive AND no longer referenced by a surviving run. |

**System-only** (protected; edited in `config.yaml`). Retention is not a per-profile
knob.

---

## 7. Quick "which setting do I change to…" map

| Goal | Setting(s) |
|---|---|
| Spend less per run | raise `scoring.min_match_score`; lower `scoring.max_scored`; turn on `search.relevance_filter`; keep `scoring.auto_interview_prep` off; assign cheaper `agents.*` models |
| See more / broader matches | lower `scoring.min_match_score`; raise `scoring.max_scored`; add `search.titles` / `search.locations` |
| Focus on one career track | set `scoring.tracks` to the subset; set `scoring.career_track` emphasis |
| Cut noise / mismatches | `search.relevance_filter`, `search.exclude_senior`, `search.{min,max}_years_experience`, `search.max_posting_age_days` |
| Curate before paying to score | `scoring.manual_selection` |
| Get higher-quality (costlier) reasoning | upgrade `agents.<name>.model` (within the cost-cap allowlist for high-volume agents) |
| Add reliable, real apply links | `scrapers.{greenhouse,lever,workday}.companies` |
| Get full JDs for cleared / senior roles (vs truncated snippets) | `scrapers.workday.companies` (ADR-101) |
| Keep less / more history | `retention.*` (system-wide, manual purge) |

---

## See also

- [business_rules.md](business_rules.md) — the rules these settings drive
- [architecture/config_model.md](architecture/config_model.md) — config system
  design, defaults, and ceilings (current values)
- [`config/config.example.yaml`](../config/config.example.yaml) — the documented
  template (copy to `config.yaml`)
- [model_recommendations.md](model_recommendations.md) — per-agent model picks +
  escalation order
- [cost_troubleshooting.md](cost_troubleshooting.md) — diagnosing cost surprises
- [architecture/adr/ADR-000-index.md](architecture/adr/ADR-000-index.md) — the
  decisions behind each setting
