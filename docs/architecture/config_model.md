# Configuration Model – jobsearchagent-v2

---

## 1. Purpose

This document defines how configuration is managed in **jobsearchagent-v2**.

The system must balance:

* stability (predictable defaults)
* flexibility (user customization)

---

## 2. Core Principle

```text
Effective Config = YAML Defaults + DB Overrides
```

Resolution order:

```text
User DB Overrides
↓
config.yaml Defaults
↓
Hardcoded Fallbacks
```

---

## 3. Configuration Layers

| Layer                  | Purpose                 |
| ---------------------- | ----------------------- |
| config.yaml            | system defaults         |
| database (user_config) | user preferences        |
| runtime                | effective merged config |

---

## 4. YAML Configuration (Static)

Used for:

* LLM providers
* model selection
* system limits
* default thresholds
* feature flags

Example:

```yaml
search:
  max_jobs: 20

limits:
  max_llm_calls: 50
  max_review_rounds: 3
```

---

## 5. User Configuration (Dynamic)

Stored in DB.

Used for:

* preferred roles (`search.titles`/`roles`) — ADR-064: these drive the profile's Adzuna discovery
* preferred locations (`search.locations`) — stored one-per-line so "City, State" is preserved
* search keywords
* experience targeting (ADR-065): a `[min, max]` years window via `search.min_years_experience` / `search.max_years_experience` (0 = that bound off) plus `search.exclude_senior` (bool; drops senior roles). Per-profile, off by default; postings that don't state experience are kept.
* relevance pre-filter (ADR-079): `search.relevance_filter` (bool, default off). When on (and `scoring.manual_selection` off), discovery casts the wide net and one cheap LLM pass drops clear seniority/relevance mismatches before scoring. Profile-relative + bidirectional (too_senior / too_junior / unrelated). Read via `get_relevance_filter(state)`.
* posting-age cap (ADR-080): `search.max_posting_age_days` (int, `0`/absent = off). Deterministic filter at discovery (upstream of the relevance filter AND scoring) dropping postings older than N days; postings with no parseable `posted_at` are kept. Stale postings correlate with dead apply links. No network fetch.
* location filter (ADR-103): `search.restrict_to_profile_locations` (bool, **default on**). Deterministic filter at discovery dropping postings confidently resolved to a country the profile's own `search.locations` did not ask for. Closes the ATS-direct global-board leak (Greenhouse/Lever return a company's worldwide listings with no location gate). Profile-derived (correct for non-US profiles), uniform across sources, keep-on-ambiguity (unparseable location / bare "Remote" / undetectable scope are kept). No network fetch.
* job limits (within bounds)
* scoring preferences (`scoring.min_match_score` — the per-profile lever for non-senior personas)
* active scoring tracks (ADR-071): `scoring.tracks` — the subset of `["ic","architect","management"]` a profile pursues. Default all three (Primary unchanged). Inactive tracks are not scored, do not gate deep review, and are hidden in the UI. Read via `get_active_tracks(state)`; distinct from `scoring.career_track` (weighting emphasis)
* ATS target companies (ADR-098): `scrapers.{greenhouse,lever}.{companies,enabled}` — the per-profile list of ATS-direct boards to pull from, managed in the Settings "Target companies" section with verify-on-add. Resolved per run from `effective_config` (an edit applies next run with no reload); a profile override **replaces** the default list (the deep-merge replaces a non-dict list value), a new profile inherits the operator-set default batch (ADR-097)
* tailoring style

---

## 6. Database Table

```sql
CREATE TABLE user_config (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    config_key TEXT NOT NULL,
    config_value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## 7. Config Service

Location:

```text
app/services/config_service.py
```

Core function:

```python
def get_effective_config(user_id: str) -> dict:
    yaml_config = load_yaml()
    overrides = load_user_overrides(user_id)  # rows for THIS user_id only
    return merge(yaml_config, overrides)
```

### Two-layer per-user merge (ADR-062)

Configuration is exactly two layers:

```
config.yaml defaults  ->  user_config (user_id = X: per-user overrides)
```

Because all pre-existing data (including config overrides) was ported to user
`"0"`, there is no separate "system-wide" override layer: the old
`user_config.user_id IS NULL` rows were migrated to `"0"`. Each profile's
overrides are keyed `user_{user_id}__{config_key}` so re-saves upsert in place.

- A newly created profile (id ≥ 1) has no overrides and runs on pure YAML
  defaults until it sets its own.
- Protected keys (`_PROTECTED_KEYS`) and the ADR-061 ceiling clamps apply to the
  merged result exactly as before and remain sourced from YAML, so they are
  shared by every profile.
- **Per-agent model/provider overrides (ADR-053/058) ride this per-user layer** —
  they are just `agents.{name}.{provider,model}` keys under the user's id. Under
  sequential use, the agent registry is rebuilt from the active user's effective
  config on profile switch / run kickoff (a rebuild, not a partition).

---

## 7b. Configurable funnel-width keys (ADR-061)

Three keys control how wide the discover -> score -> tailor funnel is. All three
are merged the standard three-tier way (yaml default -> user_config per-user
(ADR-062) -> per-run `effective_config`) and clamped to a hard ceiling for cost
safety.

| Key | Meaning | Default | Hard ceiling |
|---|---|---|---|
| `scoring.max_scored` | How many jobs get research + scoring. In auto mode this is also the discovery cap. | 10 (`MAX_JOBS_PER_RUN`) | 25 (`MAX_SCORED_CEILING`) |
| `search.max_discovered` | Wide discovery net for manual-selection (ADR-060) OR the relevance pre-filter (ADR-079). Ignored in plain auto mode. | 50 | 50 (`MAX_DISCOVERED_JOBS`) |
| `search.relevance_filter` | Opt-in reasoning pre-filter (ADR-079): one cheap LLM pass drops seniority/relevance mismatches before scoring. Bool. | off | n/a (bool) |
| `search.max_posting_age_days` | Opt-in staleness cap (ADR-080): drop postings older than N days at discovery. Int; 0 = off. Keeps postings with no parseable date. | off | n/a (days) |
| `scoring.auto_interview_prep` | Auto-run the in-graph interview coach (ADR-085, cost). Off = on-demand only (`POST .../interview-prep`); on = fires when a selected job clears `min_match_score`. Read via `get_auto_interview_prep(state)`. Bool. | off | n/a (bool) |
| `search.max_jobs` | Discovery-SERVICE backstop (how many postings the scraper layer returns). Not a user-facing knob; the precise per-run caps are applied in the nodes. | 50 | 50 (`_SYSTEM_MAX_JOBS`) |

Clamping happens in two places: `ConfigService._enforce_limits` (the per-user
merged config) and the `app/workflows/limits.py` helpers `get_max_scored()` /
`get_max_discovered_jobs()` (authoritative workflow gate — per-run config can
arrive un-clamped because the UI builds it directly). `MAX_LLM_CALLS_PER_RUN`
remains the absolute backstop.

---

## 8. Guardrails

The backend must enforce limits:

```python
max_jobs = min(user_value, SYSTEM_MAX_JOBS)
```

Users must NOT be able to modify:

* LLM models
* prompt versions
* safety thresholds
* reflection limits
* cost limits

---

## 9. UI Integration

UI allows editing:

* search preferences
* job limits (bounded)
* tailoring preference

UI must NOT:

* edit YAML directly
* expose system-level config

---

## 10. Injection into Workflow

Effective config is injected into workflow state at kickoff. The kickoff body
carries only the subtrees the caller assembled (the Start-run UI sends
`scoring` + `search`), so `start_workflow` resolves the FULL per-run config by
deep-merging that partial over the profile's complete effective config:

```python
# app/api/routers/workflows.py::start_workflow
state.effective_config = ConfigService().resolve_run_config(user_id, body_overrides)
# == deep_merge(get_effective_config(user_id), body_overrides), overrides win,
#    limits re-enforced
```

This is the authoritative per-run resolution. It guarantees that any
un-overridden per-profile subtree the caller omits — notably `scrapers`
(ADR-098 ATS company lists) — still reaches `discover_jobs` from the profile
instead of falling back to a system default. Before BUG-012 the kickoff persisted
the UI-built partial config as-is, so the per-profile `scrapers` subtree was
dropped and ATS discovery silently used the system curated batch
(`bugs/BUG-012-kickoff-drops-per-profile-scrapers-subtree.md`).

Agents receive only relevant portions.

---

## 11. Observability

Config used for a run should be logged:

```text
workflow_id
config_snapshot
```

---

## 12. Final Principle

Configuration must be:

```text
controlled
traceable
bounded
user-friendly
```