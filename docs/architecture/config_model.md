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

* preferred roles
* preferred locations
* search keywords
* job limits (within bounds)
* scoring preferences
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
    overrides = load_user_overrides(user_id)
    return merge(yaml_config, overrides)
```

---

## 7b. Configurable funnel-width keys (ADR-061)

Three keys control how wide the discover -> score -> tailor funnel is. All three
are merged the standard three-tier way (yaml default -> user_config system-wide
-> per-run `effective_config`) and clamped to a hard ceiling for cost safety.

| Key | Meaning | Default | Hard ceiling |
|---|---|---|---|
| `scoring.max_scored` | How many jobs get research + scoring. In auto mode this is also the discovery cap. | 10 (`MAX_JOBS_PER_RUN`) | 25 (`MAX_SCORED_CEILING`) |
| `search.max_discovered` | Manual-selection (ADR-060) wide discovery net. Ignored in auto mode. | 50 | 50 (`MAX_DISCOVERED_JOBS`) |
| `search.max_jobs` | Discovery-SERVICE backstop (how many postings the scraper layer returns). Not a user-facing knob; the precise per-run caps are applied in the nodes. | 50 | 50 (`_SYSTEM_MAX_JOBS`) |

Clamping happens in two places: `ConfigService._enforce_limits` (system-wide
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