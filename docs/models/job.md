# models/job.py — Core Job Data Model

## Purpose

Defines the `Job` Pydantic model — the single data shape that flows through every part of the system. Scrapers create `Job` objects, Claude scores them, and the database persists them. Also defines all enums and sub-models related to a job posting.

## Agentic Pattern: Pipeline State Machine

`ApplicationStatus` is an explicit state machine enum that tracks a job through its lifecycle:

```
NEW → SCORED → APPLIED → REJECTED
                       → OFFER
```

Each state transition is intentional:
- `NEW` — set when a scraper creates the job
- `SCORED` — set by `ScoringAgent` after Claude evaluates it
- `APPLIED` — set manually by the user via `--tailor` command
- `REJECTED` / `OFFER` — outcome tracking (set manually)

This makes it trivial to query "which jobs need scoring?" (`get_by_status(NEW)`) and "which jobs am I actively pursuing?" (`get_by_status(APPLIED)`).

## Enums

### `JobSource`
| Value | Meaning |
|---|---|
| `linkedin` | Manually pasted URL from `inbox/linkedin.txt` |
| `indeed` | From Adzuna API (which aggregates Indeed) |
| `glassdoor` | Glassdoor RSS (legacy, replaced by Adzuna) |
| `ladders` | Scraped from Ladders.com |

### `WorkMode`
| Value | Meaning |
|---|---|
| `remote` | Fully remote |
| `hybrid` | Mix of remote and in-office |
| `onsite` | Full-time in-office |

### `ApplicationStatus`
| Value | Who sets it |
|---|---|
| `new` | Scraper (default) |
| `scored` | ScoringAgent |
| `applied` | User via `--tailor` |
| `rejected` | User manually |
| `offer` | User manually |

### `CareerTrack`
| Value | Role types targeted |
|---|---|
| `ic` | Senior / Staff / Principal Engineer |
| `architect` | Solutions / Principal / Enterprise Architect |
| `management` | Senior Manager / Director / Head of Eng / VP |

## Sub-Models

### `SalaryRange`
Optional salary information. All fields are optional because most job postings omit salary.

| Field | Type | Notes |
|---|---|---|
| `min` | `int?` | Minimum salary in the posting |
| `max` | `int?` | Maximum salary in the posting |
| `currency` | `str` | Defaults to `"USD"` |

### `TrackScore`
Claude's evaluation for a single career track.

| Field | Type | Notes |
|---|---|---|
| `score` | `int` | 0–100, validated by Pydantic (`ge=0, le=100`) |
| `summary` | `str` | One sentence explaining the score |
| `recommended` | `bool` | True if score >= 65 |

### `TrackScores`
Container for all three track scores. Each track starts as `None` until scored.

| Field | Type |
|---|---|
| `ic` | `TrackScore?` |
| `architect` | `TrackScore?` |
| `management` | `TrackScore?` |

### `BatchJobScore`
Used to parse Claude's batch scoring response. `job_index` matches the `<job index="N">` tag in the prompt so scores can be mapped back correctly.

## Main Model: `Job`

### Identity Fields
| Field | Type | Notes |
|---|---|---|
| `id` | `int?` | `None` until inserted into the database |
| `url` | `str` | Canonical URL — used as the deduplication key |
| `source` | `JobSource` | Which scraper found it |

### Metadata Fields
| Field | Type | Notes |
|---|---|---|
| `title` | `str` | Job title as listed |
| `company` | `str` | Company name |
| `location` | `str?` | Location string from the posting |
| `state` | `str?` | US state abbreviation auto-extracted from `location` (e.g. `"GA"`, `"TX"`). Set by `_fill_state` validator — no scraper changes required. `None` for remote or non-US jobs. |
| `work_mode` | `WorkMode?` | Inferred from title/description text |

### Content Fields
| Field | Type | Notes |
|---|---|---|
| `description` | `str?` | Full job description, HTML-stripped, fed to Claude |
| `raw_html` | `str?` | Original HTML, kept for debugging |
| `salary` | `SalaryRange?` | `None` if not in the posting |

### Score and Status Fields
| Field | Type | Default |
|---|---|---|
| `scores` | `TrackScores` | `TrackScores()` — all tracks None |
| `status` | `ApplicationStatus` | `NEW` |

### Date Fields
| Field | Purpose |
|---|---|
| `posted_at` | When the company published the job — used for staleness check |
| `expires_at` | Application deadline (rarely provided) |
| `found_at` | When our scraper picked it up — always set automatically |
| `applied_at` | When you submitted — set when status → APPLIED |

### Validator: `_fill_state`

A `@model_validator(mode="after")` that automatically calls `extract_us_state(self.location)` when a `Job` is constructed and `state` is not already set. This means all three scrapers get state extraction for free — they only need to populate `location`, and the model fills `state` automatically.

### Computed Property: `is_stale`

Returns `True` if `posted_at` is more than 30 days ago. Jobs failing this check are skipped by `ScoringAgent` to avoid wasting tokens on expired listings.

## Pydantic Config

```python
model_config = ConfigDict(
    populate_by_name=True,   # allows setting fields by Python name
    use_enum_values=True,    # serialises enums as strings ("new" not ApplicationStatus.NEW)
)
```

`use_enum_values=True` keeps the database and JSON output clean — enum values are stored as their string representations.
