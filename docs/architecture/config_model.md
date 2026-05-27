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
* job limits (within bounds)
* scoring preferences (`scoring.min_match_score` — the per-profile lever for non-senior personas)
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
| `search.max_discovered` | Manual-selection (ADR-060) wide discovery net. Ignored in auto mode. | 50 | 50 (`MAX_DISCOVERED_JOBS`) |
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

Effective config is injected into workflow state:

```python
state.effective_config = get_effective_config(user_id)
```

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