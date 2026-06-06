# scrapers/base.py — Abstract Base Scraper

## Purpose

Defines `BaseScraper` — the abstract contract that every scraper must implement. Provides a consistent interface so `main.py` can run all scrapers in a loop without knowing their internal details.

## Design

```python
class BaseScraper(ABC):
    @abstractmethod
    def scrape(self) -> list[Job]:
        ...
```

This is the classic **Template Method** / **Strategy** pattern. `main.py` calls `scraper.scrape()` on each scraper and combines the results. The scraper handles all source-specific details internally.

## What Subclasses Must Implement

### `scrape() → list[Job]`

Fetches job postings from the source and returns them as `Job` objects. At minimum, each job must have `url`, `source`, `title`, and `company`. The `description` field should be populated if it's readily available from the source.

## What the Base Class Provides

### `log_result(jobs)`
Called by each subclass after `scrape()` to log `"<name> scraper found N jobs"`. Consistent log format across all sources.

### `self.logger`
A named logger (`scrapers.<name>`) automatically created in `__init__`. All scraper log messages appear under their scraper name in the log file.

## Concrete Subclasses

| Class | Source | Method |
|---|---|---|
| `LinkedInScraper` | LinkedIn | Reads URLs from the LinkedIn inbox, fetches HTML |
| `AdzunaScraper` | Adzuna API | REST JSON API — most reliable source |

`LaddersScraper` was removed with the v1 runtime (ADR-063). v2 adds two ATS-direct
subclasses of this same `BaseScraper` — `GreenhouseScraper` and `LeverScraper`
(`app/services/ats_scrapers.py`, ADR-081) — source-of-truth employer boards with
live apply links.
