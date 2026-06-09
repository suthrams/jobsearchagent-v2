# ADR-095: Best-Effort Dead-Link Filter (Opt-in Discovery Step)

## Status

Accepted (2026-06-08). One opt-in config knob + a deterministic-ish network filter;
no new agent or endpoint. Complements ADR-080 (posting-age proxy), ADR-081 (ATS-direct
sources), ADR-093 (apply-link reliability UI).

## Context

A scored match is useless if its apply link is dead, and the ADR-093 "where to focus"
strip makes this worse — it can surface a top-3 pick whose link 404s. Full link
verification was previously rejected as unreliable (Adzuna 429s the probes, apply pages
are JS-gated so a 200 doesn't mean the job is live, soft-expiry — the ADR-080/081
dead-link arc), so the system shipped *proxies* (posting age) and *resilience* (the
ADR-093 source badge + "find the live posting" fallback), not a checker.

The owner asked for a **best-effort** checker that drops the *clearly* dead jobs
without the false positives that killed full verification.

## Decision

### `app/services/dead_link_filter.py`

`check_link(url) -> "dead" | "alive" | "unknown"` does one redirect-following httpx GET
with a short timeout (4s):
- **dead** — final status 404/410, OR a 200 page whose body contains a known
  closed-job marker ("no longer available", "this position is no longer", ...).
- **alive** — 200 with no closed marker.
- **unknown** — timeout, connection error, DNS failure, 429, 5xx, or a non-http URL.

`filter_dead_links(postings)` checks URLs concurrently (capped at 10 workers, shared
client) and **drops only "dead"**; "alive" and "unknown" are kept. This is the
never-lose-the-run contract: a transient or ambiguous signal never drops a job (the
exact failure mode that made full verification unusable).

### Wiring — opt-in at discovery

A new `search.drop_dead_links` flag (default **off**). The discovery node reads it and
threads it to `JobDiscoveryService.discover_with_stats`, which runs the filter **after
dedup, before the cap** (fewer URLs to check; dead ones removed so good ones fill the
cap). Counts land in `discovery_stats.dead_link_dropped` + a `dead_link_samples` audit.
It is network I/O, so it only runs when the profile opts in; the UI surfaces it as a
"Drop jobs whose apply link is dead" checkbox in New search, with help noting the added
latency and the conservative (keep-on-ambiguity) behavior.

## Consequences

- Clearly-dead postings (pulled reqs returning 404/410, ATS "no longer available"
  pages) are removed before scoring and before the focus strip — so a top pick no
  longer points at a broken link.
- It is best-effort, not exhaustive: JS-gated "ghost" 200s and aggregator redirects
  that resolve to a generic search page are kept (no false drop). Posting-age (ADR-080)
  + ATS-direct (ADR-081) + the ADR-093 fallback still cover the rest.
- Adds latency when enabled: one bounded web request per discovered job (concurrent,
  4s timeout, ≤10 workers). Off by default, so default runs are unaffected.

## PSSR

- **Performance:** bounded — short timeout, capped concurrency, runs post-dedup; off by
  default. **Cost:** zero LLM; only HTTP.
- **Security/Privacy:** GETs the job's own apply URL with a generic UA; no PII sent. No
  credentials. Honors redirects to the real posting.
- **Reliability:** never-lose-the-run — drops only on hard signals (404/410/closed
  marker); every transient/ambiguous outcome keeps the job. `check_link` never raises.

## Tests

- `tests/v2/test_dead_link_filter.py` — `check_link` verdicts (404/410 dead, 200+marker
  dead, clean 200 alive, 429/5xx/3xx + timeout/connection/DNS + non-http = unknown/keep)
  and `filter_dead_links` (drops only "dead", keeps alive+unknown, sample/empty).
- `tests/v2/test_adr064_discovery.py` — node mock updated to thread `drop_dead_links`.
