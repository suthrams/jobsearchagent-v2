# ADR-057: Restore Per-Job Exclusion (v1 Design) as a Pipeline Filter

## Status
Accepted

## Context

v1 had per-job exclusion. The `jobs` table carried two columns:

```sql
excluded         INTEGER NOT NULL DEFAULT 0   -- 1 = hidden from all views
excluded_reason  TEXT                         -- Why the job was excluded
```

Users marked jobs they didn't want to pursue, dashboard analytics filtered
them out via `WHERE (excluded = 0 OR excluded IS NULL)`, and the design
held up across the lifetime of v1.

v2 dropped it. CLAUDE.md states:

> No application tracking features — Apply / Save / status fields are
> intentionally out of scope. The user's career decision-making point
> stays human-owned.

The intent of that rule is correct: the system should not turn into a
half-built ATS that records when the user applied, which recruiter they
talked to, what stage they reached. Those are outcomes about user
behavior that a research tool has no business owning.

But the rule conflated two distinct concerns:

| Concern | What it captures | Direction | Status in v2 |
|---|---|---|---|
| **Application tracking** | Apply date, recruiter contact, status transitions | Outcomes the system records ABOUT the user | Out of scope (correctly) |
| **Pipeline filter (exclude)** | "Hide this job from my views and stop processing it" | Signal the user gives TO the system | Also out of scope (in error) |

Without exclusion in v2, three real problems compound across runs:

1. **Cost waste.** A high-scoring job the user has already decided
   against still consumes deep-review LLM calls when re-discovered.
   Adzuna can surface the same posting on a different day; v2's existing
   URL dedup (`JobDiscoveryService.deduplicate` at
   `app/services/job_discovery_service.py:151`) drops it from re-insert,
   but if a new posting arrives at a NEW url for the same role+company,
   only an `excluded` flag on the prior row helps.
2. **Signal noise.** Top Matches, IC / Architect / Management Track, and
   Companies views all keep showing the rejected jobs in perpetuity.
   After 5-10 runs the analytics surfaces stop being useful.
3. **No feedback loop.** The excluded set is data — the system could
   eventually infer "this candidate consistently de-prioritizes manager
   roles at sub-100-person companies" and adjust auto-select. Not in
   scope for this ADR but blocked by not capturing the data at all.

## Decision

Restore the v1 exclusion design directly: add `excluded` and
`excluded_reason` columns to v2's `jobs` table. No new table, no new
foreign keys. The schema and API surface are intentionally minimal so
the scope cannot drift toward application tracking.

The choice to mirror v1 instead of designing a fresh `dismissed_jobs`
table comes from two observations:
- v1's design held up; the columns are obvious and unambiguous.
- v2 already URL-dedups at discovery, so a `jobs.excluded` flag is
  load-bearing on the rows that actually exist — no need for a
  separate URL-keyed dedup table.

### What this ADR captures
- per-job `excluded` flag (0 / 1)
- optional free-text `excluded_reason` (recall only; never parsed)

### What this ADR explicitly does NOT capture
- application status (applied / phone screen / offer / rejected by company)
- application date, recruiter name, communication threads
- salary, leveling discussion, or any negotiation metadata
- automatic re-surfacing ("exclude for 30 days then revisit")
- per-company permanent block lists
- per-user partitioning (single-user system; matches the rest of v2)

If the user wants application tracking, the right tool is an ATS
(Notion, Trello, Huntr). The system stays a research and
decision-support tool.

## Schema

Two additive columns on the existing `jobs` table:

```sql
ALTER TABLE jobs ADD COLUMN excluded         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN excluded_reason  TEXT;
```

Migration: try/except `ALTER TABLE` in `init_db()`, the same pattern
ADR-055 used for `tailored_resumes` columns. No data migration needed —
existing rows default to `excluded = 0`.

No new table, no new index. Filtering uses the existing `id` PRIMARY
KEY and the new `excluded` predicate, both of which SQLite handles
without any additional index for our row counts (low thousands).

## Repository

`JobRepository` (`app/repositories/job_repository.py`) gains three methods:

```python
def set_excluded(self, job_id: str, reason: str | None = None) -> None: ...
def clear_excluded(self, job_id: str) -> None: ...
def excluded_set(self) -> set[str]:
    """All job_ids currently flagged excluded. Cheap; cached per workflow run."""
```

The existing `upsert()` does NOT touch the new columns — exclusion is
set explicitly via `set_excluded()`. This means a re-discovery upsert
preserves the prior `excluded` flag (the ON CONFLICT clause only
overwrites `normalized_job_json`).

## API

```
POST   /jobs/{job_id}/exclude     Body: {"reason": str | null}
DELETE /jobs/{job_id}/exclude     Un-exclude (rare; provided for completeness)
GET    /jobs/excluded              List excluded jobs
```

URL convention follows ADR-055's tailoring router: per-resource action
endpoints under `/jobs/{id}`, list endpoint under `/jobs/excluded`.
Lives in `app/api/routers/jobs.py` (new file or extension of an existing
jobs router).

## Pipeline Integration

Two filter points, one cheap to add:

1. **Discovery / scoring filter.** `JobDiscoveryService.deduplicate()`
   already drops URLs that exist in the DB. Extend it to also drop jobs
   whose `id` is in `excluded_set()` — fetched once at the start of the
   workflow run and passed through `WorkflowDependencies`. Excluded
   jobs never reach scoring, never consume LLM calls.

2. **UI / read-side filter.** `app/ui/db_reader.py` query helpers JOIN
   `jobs.excluded = 0` (or `IS NULL`) by default. Each read function
   accepts an `include_excluded: bool = False` parameter so an explicit
   "show excluded" toggle still works.

## UI

**Workflow Detail → Find & Score table.** Each row gains a 🚫 button
that calls `POST /jobs/{job_id}/exclude` with no required reason.
Single click; no modal. A "Show excluded (N)" caption-style toggle
reveals hidden rows for that workflow.

**Cross-run analytics (Top Matches, IC / Architect / Management Track,
Companies).** Default to hiding excluded rows. A sidebar checkbox
"Include excluded jobs" makes them reappear for audit.

**Workflow History.** Unchanged — the row count there is per-run, not
per-job, so exclusions don't affect it.

No dedicated "Excluded Jobs" management screen in v0.1 — exclusions
are visible in any view that toggles them on. If usage data shows
people want a one-stop-shop, we add a screen.

## Why this is NOT application tracking (clarification for code review)

A reviewer evaluating any future change to this surface should ask one
question: **"Does this field record an outcome about the user's
behavior toward the employer, or does it record a filter the user
gives the system?"**

| Field | Tracking? | Filter? | Allowed? |
|---|---|---|---|
| `excluded` | No (boolean filter input) | Yes | ✓ |
| `excluded_reason` | No (recall-only free text) | Yes | ✓ |
| `applied_at` | YES (records user's external action) | No | ✗ — out of scope |
| `application_status` | YES (status transitions) | No | ✗ — out of scope |
| `recruiter_name` | YES (relationship metadata) | No | ✗ — out of scope |
| `interview_dates` | YES | No | ✗ — out of scope |

Anything in the lower half of the table belongs in an ATS, not here.

## Rationale

- **v1 design held up.** Two columns, two filter clauses, single-click
  UI. There is no evidence v2 needs a different shape.
- **No new table.** The data lives next to the row it filters; SQLite
  doesn't need a new index; migration is one ALTER per column.
- **Cost saving is concrete.** A 10-job run with deep review costs
  `~$0.05–0.20`. If two of those jobs are repeats of explicitly
  excluded items at fresh URLs, that's 20–40% wasted on known-no
  jobs. Adding `excluded` to the discovery filter recovers that.
- **Multi-run hygiene.** The analytics views are a real product
  surface, and they degrade in usefulness with every run that lands
  more rejected-but-high-scoring jobs at the top.
- **Scope discipline through schema.** By writing only filter-shaped
  fields and explicitly forbidding outcome-shaped fields in the ADR,
  we preserve CLAUDE.md's intent without losing the legitimate use
  case.

## Consequences

### Positive
- Real cost waste eliminated: excluded jobs never re-trigger scoring or
  research on subsequent runs.
- Cross-run analytics become useful at scale.
- Backwards-compatible: ALTER TABLE adds columns with a 0 default,
  existing rows are unaffected, exclusion is opt-in per row.
- Sets up the data for future "preference learning" features without
  committing to them now.

### Tradeoffs
- Two new repository methods, one new router, one new UI button,
  one extension to `JobDiscoveryService.deduplicate()`, and a default-
  excluded WHERE clause in every job-read helper. Touches repository,
  API, services, and UI layers.
- Re-introduces a "v1 had it, v2 dropped it, we put it back" pattern
  CLAUDE.md is implicitly trying to avoid. Mitigated by the explicit
  scope-discipline section above and by inline comments wherever the
  new columns are read or written.
- One more filter for views to honor. A future view that forgets to
  apply it would silently surface excluded jobs. Mitigated by routing
  all job reads through `db_reader.py` helpers that take an
  `include_excluded: bool = False` parameter.

### Neutral
- Per-company exclusion intentionally not in v0.1. The use case ("I
  never want Meta jobs again") is rarer and can be approximated by
  excluding each individual Meta listing until usage data justifies
  adding a per-company surface.
- Un-exclude is API-only (no UI button) per the user's "rare use case"
  guidance.
- We adopt v1's vocabulary (`excluded` / `excluded_reason`) rather than
  introducing new terms (`dismissed`, `hidden`, `archived`). v1 already
  proved the names are clear and there is no reason to churn them.

## Implementation Notes (anticipated, not yet built)

- `app/repositories/database.py` — two `ALTER TABLE` statements added to
  `init_db()`'s migration block, wrapped in try/except `OperationalError`
  to be safe on existing DBs (same pattern as `tailored_resumes` columns
  in ADR-055).
- `app/repositories/job_repository.py` — `set_excluded()`,
  `clear_excluded()`, `excluded_set()` methods. `upsert()` left alone so
  re-discoveries do not clobber the flag.
- `app/api/routers/jobs.py` — new router with
  `POST /jobs/{job_id}/exclude`, `DELETE /jobs/{job_id}/exclude`,
  `GET /jobs/excluded`. Wire into `app/api/main.py`.
- `app/services/job_discovery_service.py` — `discover()` accepts
  `excluded_ids: set[str]` and drops jobs whose `id` is in the set
  inside `deduplicate()`. The set is fetched once at workflow start
  via `JobRepository.excluded_set()`.
- `app/workflows/nodes/discover_jobs.py` (or wherever `register_run`
  lives) — fetch the excluded set at run start; pass into the discovery
  service.
- `app/ui/streamlit_app.py` — Find & Score table gains a 🚫 button per
  row; cross-run analytics views default-hide excluded rows with a
  sidebar "Include excluded jobs" toggle.
- `app/ui/db_reader.py` — every job-read helper gains
  `include_excluded: bool = False` and a default `WHERE (excluded = 0
  OR excluded IS NULL)` clause.
- Tests:
  - `tests/v2/test_job_repository.py` — exclusion persistence, upsert
    does not clobber the flag.
  - `tests/v2/test_jobs_router.py` — endpoint contracts.
  - `tests/v2/test_job_discovery_service.py` — excluded set filters at
    discovery time.
  - `tests/v2/test_db_reader.py` — every helper honors the
    `include_excluded` flag.

## What this ADR does NOT change

- CLAUDE.md's "no application tracking features" rule still stands.
  Apply / Save / status remain out of scope.
- Tailoring decision flow (approve / revise / reject) is unrelated;
  those track the user's decision on a TAILORED DRAFT, not on a job.
- Job-selection HITL remains removed (auto-select per ADR-054).
  Exclusion is post-hoc filtering, not pre-deep-review gating.
- v2's `jobs` table primary-key (`id TEXT`) and URL-dedup logic are
  unchanged.

## References
- v1 prior art: `storage/db.py:44-45` (column definitions),
  `storage/db.py:55-56` (migrations), `storage/db.py:511-518`
  (`exclude_jobs_db`), `dashboard.py:124,174,188` (filter clauses).
- v2 dedup precedent: `app/services/job_discovery_service.py:151-165`
  (`deduplicate` already drops URLs that exist in the DB; the new
  filter slots in alongside).
- CLAUDE.md "No application tracking features" rule — clarified into
  filter-vs-tracker distinction, not overridden.
- ADR-054 — Allow Deep Review for All Qualifying Jobs (auto-select; the
  cost-amplifying change that makes pipeline filtering more valuable).
- ADR-055 — On-Demand Tailoring as Out-of-Graph (precedent for the
  try/except `ALTER TABLE` migration pattern in `init_db()`).
