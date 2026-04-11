# models/config_schema.py — Configuration Schema

## Purpose

Pydantic models that mirror the structure of `config/config.yaml`. The config file is loaded once at startup and validated against these models. If `config.yaml` has missing fields, wrong types, or invalid values, Pydantic raises a clear error before any scraping or API calls begin — fail-fast at the boundary.

## Why Pydantic for Config?

Raw `yaml.safe_load()` returns an untyped dict. Without schema validation:
- Missing keys cause `KeyError` deep inside the code — hard to debug
- Wrong types (e.g., string where int is expected) fail silently or crash later
- No IDE autocomplete

With Pydantic, every config value is typed, optional fields have sensible defaults, and the error message tells you exactly which key is wrong.

## Model Hierarchy

```
AppConfig
├── search:    SearchConfig
│   └── years_of_experience: YearsOfExperience (optional)
├── salary:    SalaryConfig
├── tracks:    TracksConfig
├── claude:    ClaudeConfig
│   ├── max_tokens:  MaxTokensConfig
│   └── temperature: TemperatureConfig
├── scrapers:  ScrapersConfig
│   ├── linkedin: LinkedInConfig
│   ├── adzuna:   AdzunaConfig
│   └── ladders:  LaddersConfig
├── storage:   StorageConfig
└── staleness: StalenessConfig
```

## Key Models

### `AppConfig` (root)
The root object returned by `load_config()` in `main.py`. Every section of `config.yaml` maps to a typed sub-model.

### `TracksConfig`
Controls which career tracks are scored. Disabling a track saves API tokens.

| Field | Default | Meaning |
|---|---|---|
| `ic` | `True` | Score against Senior/Staff/Principal Engineer criteria |
| `architect` | `True` | Score against Solutions/Principal Architect criteria |
| `management` | `True` | Score against Manager/Director/VP criteria |

### `SalaryConfig`
| Field | Default | Meaning |
|---|---|---|
| `min_desired` | `150000` | Minimum desired salary — Claude deducts 10 pts if below this |
| `currency` | `"USD"` | Currency code used in prompts |
| `ignore_if_missing` | `True` | Don't penalise jobs that omit salary |

### `ClaudeConfig`
| Field | Default | Meaning |
|---|---|---|
| `model` | `"claude-sonnet-4-6"` | Claude model for all operations |
| `max_tokens` | `MaxTokensConfig` | Per-operation token limits |
| `temperature` | `TemperatureConfig` | Per-operation temperatures |

#### `MaxTokensConfig`
| Operation | Default | Rationale |
|---|---|---|
| `resume_parsing` | 1,000 | Profile JSON is compact |
| `job_scoring` | 3,500 | Covers 10 score objects + summaries (~300 tokens each) |
| `resume_tailoring` | 2,000 | Longer freeform content |

#### `TemperatureConfig`
| Operation | Default | Rationale |
|---|---|---|
| `resume_parsing` | 0.1 | Deterministic extraction |
| `job_scoring` | 0.1 | Consistent scoring across runs |
| `resume_tailoring` | 0.3 | Slightly more natural language |

### `AdzunaConfig`
| Field | Default | Meaning |
|---|---|---|
| `enabled` | `True` | Can be disabled without removing config |
| `country` | `"us"` | ISO country code for Adzuna endpoint |
| `keywords` | `[]` | Search terms used for every local location search |
| `locations` | `[]` | Cities/states to search, e.g. `["Atlanta, GA", "Houston, TX"]`. One API call per keyword × location combination. |
| `radius_km` | `80` | Search radius around each location in kilometres |
| `results_per_page` | `10` | Results per keyword per call (max 50 on free tier) |
| `remote_keywords` | `[]` | Keywords for US-wide remote search (no location filter, one call per keyword) |

> **Quota planning:** Total calls per run = `(len(locations) × len(keywords)) + len(remote_keywords)`. Keep below 100 (free tier daily limit).

### `StorageConfig`
| Field | Default | Meaning |
|---|---|---|
| `database` | `"data/jobs.db"` | SQLite database path |
| `tailored_resumes_dir` | `"output/resumes"` | Where tailored resume .txt files are saved |
| `logs_dir` | `"output/logs"` | Where run logs are written |

### `StalenessConfig`
| Field | Default | Meaning |
|---|---|---|
| `max_days` | `30` | Jobs older than this are skipped during scoring |

## Usage in Code

```python
config = AppConfig.model_validate(yaml.safe_load(config_file))

# Typed access — IDE autocomplete works
config.claude.model           # "claude-sonnet-4-6"
config.scrapers.adzuna.country # "us"
config.tracks.ic               # True
config.salary.min_desired      # 150000
```
