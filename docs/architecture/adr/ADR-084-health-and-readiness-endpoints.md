# ADR-084: Liveness + Readiness Health Endpoints (`/health`, `/readyz`)

## Status

Accepted (implemented) (2026-06-06). `GET /health` + `GET /readyz`
(`app/api/routers/health.py`, `app/services/readiness.py`), the api_requests
exclusion (`app/api/main.py`), and the System Dashboard health tile
(`app/ui/views/system_dashboard.py`) all shipped; tests in
`tests/v2/test_readiness.py`. Companion design doc:
[`health_check_design.md`](../health_check_design.md).

## Context

The API exposes ~30 REST endpoints across 10 routers, but `app/api/main.py` mounts
those routers and nothing else: there is **no liveness or readiness endpoint** (no
`/health`, `/readyz`, `/livez`, no root route), and no way for an external monitor,
load balancer, or a human to ask "is the service up, and are its dependencies
healthy?".

What we *do* have is **passive, traffic-driven** observability (ADR-074 Gap 5): the
`@app.middleware("http")` records every request into `api_requests`, and the System
Dashboard's "API requests" section shows error rate, p95 latency, and a per-route
breakdown. That is valuable but retrospective:

- an endpoint that is **broken but never called** shows nothing;
- nothing **external** can probe the service;
- the only "readiness" signal today is the startup Phase-7 gate
  (`ANTHROPIC_API_KEY` present -> live agents + `SqliteSaver`; absent -> mocked +
  `MemorySaver`), which is a boot-time decision, not a runtime endpoint.

We explicitly do **not** want synthetic probing of all 30 endpoints. Most are `POST`
and mutating: probing `POST /workflows` would start a real, paid workflow run.
Health must probe the **shared dependencies** every endpoint relies on, not each
route.

## Decision

Add two dedicated, unauthenticated infrastructure endpoints, plus a dashboard
surface.

### A. `GET /health` - liveness

Always returns `200` if the process is serving. Performs **no dependency I/O**. Body:

```json
{ "status": "ok", "service": "jobsearchagent-v2", "version": "<optional>" }
```

This is the cheap "is the process alive" probe for a load balancer / uptime monitor.

### B. `GET /readyz` - readiness

Runs cheap checks against the **shared dependencies** and returns an aggregate plus
per-check detail. Returns `200` when the service can do its job, `503` only when a
**critical** dependency is down.

Checks (each: `{ok, detail, latency_ms?}`):

| Check | Dependency | Critical? | Healthy when |
|---|---|---|---|
| `database` | `data/v2.db` (`DEFAULT_DB_PATH`) | **yes** | a connection opens and `SELECT 1` succeeds |
| `agent_provider` | `ANTHROPIC_API_KEY` (Phase-7 gate) | no | present -> `mode: "live"`; absent -> `mode: "mock"` (degraded, still servable) |
| `adzuna` | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | no | both present -> discovery available; else degraded |
| `openai` | `OPENAI_API_KEY` | no (optional) | informational only; never affects status |

Aggregate status / HTTP code:

- **`down` -> 503**: the `database` check failed. The service cannot serve.
- **`degraded` -> 200**: DB ok, but a capability is unavailable - running in mock mode
  (no `ANTHROPIC_API_KEY`) or Adzuna unconfigured. The service is up; some features are
  limited.
- **`ready` -> 200**: all critical + capability checks green.

### C. Unauthenticated and not profile-scoped

`/health` and `/readyz` are infrastructure endpoints. They do **not** take the
ADR-062 `?user_id=` seam, do not resolve identity, and do not touch per-profile data.

### D. Secret-safe by construction

`/readyz` reports dependency **presence and mode only** (`live`/`mock`,
`configured`/`not configured`) - **never** the value of any API key, and never any
PII. This is the same "summaries, not contents" rule the security-event descriptions
follow (ADR-069/073).

### E. Excluded from `api_requests` recording

The observe middleware skips `/health` and `/readyz`. Probes would otherwise flood
the `api_requests` table (frequent polling) and a `503` from `/readyz` would
wrongly inflate the dashboard's **API error rate**. Health is a separate signal from
request observability.

### F. Dashboard surface

A **"System health"** tile at the top of the System Dashboard calls `GET /readyz`
through `api_client` (ADR-075: the UI reads through the API) and renders the overall
status plus each dependency check, alongside the existing API error-rate metric.
Because readiness is **live infra state** (not historical DB data), this is a live
call, not a `system_health` DB read.

### G. Out of scope (v1)

- Synthetic per-endpoint probing of the 30 routes (mutating endpoints; cost).
- Persisted health history / trend (no `health_checks` table) and scheduled
  background probing.
- Alerting / paging.

Passive per-endpoint health (error rate, p95, per-route) stays exactly as ADR-074
shipped it; this ADR adds the active liveness/readiness layer on top.

## Options considered

1. **Synthetic probing of all 30 endpoints** - rejected. Most endpoints mutate state
   (`POST /workflows` starts a paid run); probing them is unsafe and expensive.
   Shared-dependency checks give the same "is the plumbing healthy" answer safely.
2. **Third-party APM / uptime (Datadog, UptimeRobot, k8s probes)** - out of scope,
   but `/health` + `/readyz` are precisely what such a tool would poll, so this ADR
   *enables* that later with no rework.
3. **Persist health snapshots to a `health_checks` table** - deferred. Live-only is
   sufficient for a single-node app; revisit alongside scheduled probing.
4. **Fold readiness into an existing endpoint (e.g. `/config`)** - rejected. Dedicated
   `/health` + `/readyz` is the universal convention monitors expect.

## Consequences

### Positive

- External monitors / load balancers can probe liveness and readiness.
- "Is it up? Is it ready?" is answerable on demand, including the otherwise-invisible
  states: **mock mode** and **missing Adzuna credentials**.
- Near-zero cost: liveness does no I/O; readiness does one `SELECT 1` plus env reads.
- Lays the groundwork for APM/alerting with no redesign.

### Tradeoffs / limits (honest)

- Not active per-endpoint coverage: a broken-but-uncalled **mutating** endpoint still
  only surfaces via the passive error rate once it is actually called.
- Live-only: no health history or trend in v1.
- Single-node assumption: no per-replica aggregation (matches the rest of the system).

### Neutral

- `/readyz` reveals coarse infra state unauthenticated. Acceptable and standard: it
  exposes no secrets and no PII. Recorded in `security.model.md`.

## References

- ADR-074 (passive `api_requests` observability - the layer this complements)
- ADR-075 (UI reads through the API - how the dashboard tile fetches `/readyz`)
- ADR-073 (System Dashboard - where the tile lives)
- ADR-062 (identity seam - health endpoints are the documented exemption)
- Design: [`health_check_design.md`](../health_check_design.md)
