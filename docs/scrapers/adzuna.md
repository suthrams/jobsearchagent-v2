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

1. **Local search** — uses `config.location` + `config.radius_km`, one call per keyword in `config.keywords`
2. **Remote search** — no location filter, appends `"remote"` to each keyword in `config.remote_keywords`

This ensures you capture both local opportunities and fully remote roles from across the country.

Both keyword lists support optional IoT-specific entries (commented out by default in `config.yaml`). Uncomment the IoT blocks to add searches like `"IoT architect"` and `"head of IoT"` to both local and remote passes.

## Title Filtering

Before creating a `Job` object, two filters run on the title:

**Inclusion filter** (`RELEVANT_TITLE_KEYWORDS`) — title must contain at least one:
`engineer`, `architect`, `director`, `manager`, `principal`, `staff`, `lead`, `head of`, `vp`, `cloud`, `data`, `devops`, `solutions`, `technical`, `developer`, `iot`, `embedded`, `connected devices`, `edge computing`

**Exclusion filter** (`EXCLUDED_TITLE_KEYWORDS`) — title must NOT contain:
`presales`, `sales manager`, `sales engineer`, `account manager`, `java developer`, `electrical engineer`, etc.

This pre-filters noise at the scraper level before jobs reach the more expensive Claude scoring stage.

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

Free tier: 100 calls/day. With 3 local keywords + 3 remote keywords = **6 calls per run**, giving ~16 full runs per day.

`results_per_page` defaults to 20 but can be raised to 50 (the free-tier max) in `config.yaml`.

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
