# ADR-050: Wrap v1 AdzunaScraper with a Concurrent Adapter

## Status
Accepted

## Context
Phase 8 requires concurrent Adzuna scraping. The v1 `AdzunaScraper` makes one HTTP call per `(title, location)` pair sequentially. With 14 titles × 4 locations = 56 calls, serial scraping takes ~60s.

ADR-001 prohibits modifying v1 files. The v1 scraper must stay unchanged.

## Decision
Introduce `app/services/concurrent_adzuna_scraper.py` as a thin adapter that:
1. Wraps an instance of v1 `AdzunaScraper`
2. Fans out each `(title, location)` search pair to a `ThreadPoolExecutor` (5 workers)
3. Deduplicates results across workers
4. Patches `_resolve_url` to a no-op on the wrapped instance

`JobDiscoveryService` accepts `ConcurrentAdzunaScraper` as a drop-in scraper — no changes to the service or the workflow.

## Rationale
- Preserves v1 stability (ADR-001) — the original scraper is not touched
- `JobDiscoveryService`'s scraper interface is unchanged — any scraper registered there gets the 180s safety timeout for free
- `_resolve_url` no-op prevents extra HTTP calls to resolve Adzuna redirect URLs — URLs are stored as-is and resolved at click time

## Consequences

### Positive
- Concurrent scraping without modifying v1 code
- v1 AdzunaScraper remains runnable standalone for debugging
- Deduplication in the adapter prevents duplicate job postings from parallel workers hitting the same posting

### Tradeoffs
- Extra layer of indirection between `JobDiscoveryService` and the underlying HTTP calls
- `_resolve_url` patch must be maintained if the v1 scraper method is renamed

## Implementation Notes
- `ConcurrentAdzunaScraper.make(cfg, titles)` is the factory method used in `app/api/dependencies.py`
- `JobDiscoveryService.discover()` enforces a 180s per-scraper safety timeout via `ThreadPoolExecutor.shutdown(wait=False)` — the concurrent scraper inherits this for free
