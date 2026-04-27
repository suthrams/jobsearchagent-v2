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