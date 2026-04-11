# scrapers/adzuna.py — Adzuna API Scraper

## Purpose

Fetches job listings from the [Adzuna API](https://developer.adzuna.com). Adzuna aggregates jobs from hundreds of sources including Indeed, LinkedIn, Glassdoor, and company career pages, and returns structured JSON — no HTML parsing required. This is the **primary and most reliable** scraper in the system.

## Why Adzuna Instead of Direct Scrapers?

| Concern | Direct Scraping (Indeed, Glassdoor) | Adzuna API |
|---|---|---|
| Data format | HTML — requires parsing, breaks on markup changes | Clean JSON |
| Rate limiting | Strict — IP blocks are common | Controlled API limits |
| Authentication | Not required but scrapers get blocked | App ID + Key |
| Coverage | One site each | Aggregates hundreds of sources |
| Cost | Free | Free tier: 100 calls/day |

## Authentication

Reads from environment variables set in `.env`:
```
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_api_key
```

Get credentials at [developer.adzuna.com](https://developer.adzuna.com).

## Search Strategy

Two search passes per run:

1. **Local search** — iterates over every location in `config.locations` × every keyword in `config.keywords`. One API call per combination.
2. **Remote search** — no location filter, appends `"remote"` to each keyword in `config.remote_keywords`. One call per keyword.

**Example with 4 locations and 6 keywords:**
- 4 × 6 = 24 local calls + 6 remote calls = **30 calls per run** (well within the 100/day free tier)

Results are deduplicated by URL across all location/keyword combinations, so the same posting returned by multiple searches is only stored once.

## API Quota Planning

Free tier: **100 calls/day**. Budget is: `(len(locations) × len(keywords)) + len(remote_keywords)`.

Keep this total below 100 to allow multiple runs per day. `results_per_page` defaults to 10 — raise it toward 50 (the free-tier max) only if you're getting too few results per keyword.

## Title Filtering

Before creating a `Job` object, two filters run on the title. Both filter lists are **imported from `models/filters.py`** — the single source of truth shared with `ScoringAgent`.

**Inclusion filter** (`RELEVANT_TITLE_KEYWORDS`) — title must contain at least one:
`engineer`, `architect`, `director`, `manager`, `principal`, `staff`, `lead`, `head of`, `vp`, `cloud`, `devops`, `solutions`, `developer`, `iot`, `embedded`, `connected devices`, `edge computing`

**Exclusion filter** (`EXCLUDED_TITLE_KEYWORDS`) — title must NOT contain any of:
- Sales: `presales`, `sales manager`, `sales engineer`, `account manager`, `business development`
- Non-tech management: `property manager`, `community manager`, `leasing`, `project manager`, `program manager`, `office manager`, `operations manager`, `fundraising`, `transcription`
- Non-software engineering: `electrical engineer`, `civil engineer`, `structural engineer`, `landscape architect`, `design specification`, `hvac`, `substation`
- Junior/unrelated: `intern`, `internship`, `associate engineer`, `hotel`, `medical`

This pre-filters noise at the scraper level before jobs reach the more expensive Claude scoring stage. Adding a keyword to `models/filters.py` automatically applies it in both the scraper and the scoring agent.

## URL Resolution

Adzuna returns tracking/redirect URLs. The scraper follows these with an HTTP HEAD request to resolve the actual job posting URL. This means the `url` stored in the database is the canonical company/job board URL, not an Adzuna tracking link.

```python
job.url = self._resolve_url(client, redirect_url)
```

## Work Mode Inference

Adzuna doesn't provide a structured work mode field. The scraper infers it by scanning title + description text for keywords:
- `"remote"` → `WorkMode.REMOTE`
- `"hybrid"` → `WorkMode.HYBRID`  
- `"onsite"` / `"on-site"` / `"in office"` → `WorkMode.ONSITE`

## Retry Logic

Each `_fetch_jobs()` call is decorated with `@retry` from `tenacity`:
- Up to 3 attempts
- Exponential backoff: 2s, 4s, 8s

## Rate Limit Awareness

Free tier: 100 calls/day. Budget formula: `(len(locations) × len(keywords)) + len(remote_keywords)`.

Default config: 4 locations × 6 keywords + 6 remote = **30 calls/run** → ~3 full runs per day before hitting the limit.

`results_per_page` defaults to 10. Raise toward 50 (the free-tier max) if you need more results per search. Halving `results_per_page` doesn't reduce call count but does reduce the number of noisy jobs ingested.

## Field Mapping

| Adzuna JSON field | Job field | Notes |
|---|---|---|
| `title` | `title` | — |
| `company.display_name` | `company` | Nested object |
| `location.display_name` | `location` | Nested object |
| `description` | `description` | Plain text snippet (~200 chars) |
| `redirect_url` | `url` | Resolved to canonical URL |
| `salary_min` | `salary.min` | Optional |
| `salary_max` | `salary.max` | Optional |
| `created` | `posted_at` | ISO 8601 string |
