# ADR-062: Multi-User Profiles with a Single Swappable Identity Seam

## Status

Accepted (2026-05-26).

Relates to ADR-046 (hybrid configuration model: YAML + DB overrides — this extends
the override layering to be per-user), ADR-021 (store workflow runs, not just
results — `workflow_runs` gains a populated owner), ADR-023/027 (observability and
cost tracking — now attributable per user), ADR-040 (data retention and privacy —
per-user isolation of resume and memory data), and ADR-053/058 (per-agent
provider/model selection — agent overrides become a per-user config layer).

## Context

The system was built for a single user: one `config/config.yaml`, one active
resume, one global pool of long-term memory. The user now needs to run searches
for more than one person (e.g. a second family member) where the resume differs,
the search criteria differ, and the learned memory must not cross-contaminate.

Reconnaissance of the v2 code shows the foundation is partly scaffolded but never
wired:

- `workflow_runs.user_id` and `user_config.user_id` columns already exist; both
  are always written `NULL` today.
- `ConfigService.get_effective_config(user_id=None)` and
  `ConfigRepository.get_by_user(user_id)` already thread a `user_id` parameter,
  but every caller passes `None`. `user_id IS NULL` rows function today as a
  single global override layer.
- The gaps: the `resumes` table has no `user_id` and uses a **global**
  `is_active` flag (creating any resume deactivates all others, for everyone);
  the `memory_items` table has no `user_id` and is a single global pool; the
  Streamlit UI has no profile concept; `app/ui/db_reader.py` reads all data
  globally; no API endpoint accepts or resolves a user.

The decision is to make the application multi-user with the **simplest front door
that does not foreclose a stronger one later**. The near-term need is a personal /
family setup: pick whose search this is, no passwords. But the data model and the
identity resolution point must be built so that adding real authentication later
is additive, not a rewrite.

Two clarifying constraints from the requirements discussion:

1. **Sequential use, not concurrent.** One person's search runs at a time; you
   switch profiles between runs. No simultaneous runs are required. This lets the
   existing global singletons (compiled graph, `WorkflowDependencies`, agent
   registry) stay as-is and be rebuilt on profile switch / run kickoff rather than
   partitioned per user.

2. **Isolation here is data scoping, not a security boundary.** With no
   authentication, a profile selector is cooperative: it decides *which* data a
   request reads and writes, but it does not *prevent* a determined caller from
   naming another user's id. That is acceptable for a trusted personal tool and is
   explicitly the seam where a real boundary plugs in later (see Decision E).

## Decision

### A. A static `users` table as the identity anchor, integer ids

Add one table:

```sql
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,  -- 0 reserved for pre-existing data; new users auto-increment from 1
    name        TEXT NOT NULL,        -- display name shown in the profile selector
    note        TEXT,                 -- optional human-friendly label (e.g. "New-grad SWE, west coast"); never acted on
    created_at  TEXT NOT NULL
);
```

The `note` is optional descriptive metadata for the human only — the system never
parses or acts on it. The row stays deliberately minimal: anything the system
*uses* per profile (resume, config, search defaults) lives in its existing
per-user home, not here.

Identities become real rows, never free-text literals scattered through the code.
This is the single most important extensibility decision: adding authentication
later is "attach a credential column / table to an existing `users` row," not a
data-model migration. The table is *static* in the sense that it is managed by an
explicit add action (Decision F), not derived or inferred.

**Id scheme (per the requirement).** `id` is an `INTEGER PRIMARY KEY`. The
migration seeds exactly one row with `id = 0` — the owner of all pre-existing
data. Because SQLite assigns the next rowid as `max(id) + 1`, every profile
created afterward via `POST /users` receives `1, 2, 3, ...` automatically. `id 0`
is the reserved default/legacy owner by convention; no separate `is_default`
column is needed.

**Storage of references.** The two columns that already carry a user
(`workflow_runs.user_id`, `user_config.user_id`) are declared `TEXT` and cannot be
cheaply re-typed in SQLite. To avoid type-affinity mismatches we standardize on
**the decimal-string form** (`"0"`, `"1"`, ...) for every `user_id` reference
column — the new `resumes.user_id` and `memory_items.user_id` are added as `TEXT`
too. The identity seam (Decision B) always resolves to that string, so all
comparisons are string-to-string. `users.id` itself stays `INTEGER` purely for the
clean auto-increment; it is stringified at the boundary.

### B. A single identity seam

All "who is the current user?" logic lives in exactly one place on each side of
the wire:

No HTTP headers are used. Identity travels as an explicit `user_id` value, and
all resolution lives in exactly one place on each side of the wire:

- **Backend:** a FastAPI dependency `get_current_user_id()` in a new
  `app/api/identity.py`. It reads a `user_id` **query parameter** (e.g.
  `?user_id=1`), applied uniformly to every endpoint via `Depends(...)` — it works
  the same for GET and POST, so no per-endpoint body field is needed. It falls
  back to `"0"` — the default user — when the parameter is absent (backward
  compatibility), validates the id exists in `users`, and returns the resolved
  `user_id` string. Every router depends on this; **no router parses identity
  itself.** Everything downstream consumes the *resolved* id and never cares how
  it was resolved.
- **Frontend:** `st.session_state["current_user_id"]`, set by a sidebar profile
  selector (Decision F). `app/ui/api_client.py` attaches it as the `user_id` query
  parameter (`params={"user_id": ...}`) on every request; `app/ui/db_reader.py`
  takes the same value as a function argument and filters its queries by it.

When authentication is added later, only the body of `get_current_user_id()`
changes — it reads the id from an authenticated session/token instead of the query
parameter (and the UI's selector becomes a login). Repositories, the workflow, and
read paths are untouched because they already depend only on a resolved
`user_id`. This is exactly why identity is funneled through one dependency rather
than read ad hoc in each route.

### C. Per-user data scoping (the columns and the queries)

Add `user_id TEXT` to the two tables that lack it, and start populating the one
that already has it:

- **`resumes`** gains `user_id`. The global `is_active` trick becomes per-user:
  `create(user_id, ...)` deactivates only that user's prior resumes
  (`UPDATE resumes SET is_active = 0 WHERE user_id = ?`); `get_active(user_id)`
  filters by owner. The UI resume picker lists only the active user's resumes.
- **`memory_items`** gains `user_id`. All `MemoryRepository` methods take and
  filter by `user_id`; memory ids are namespaced per user to avoid key
  collisions. Long-term learning is isolated per profile — one person's learned
  fit patterns and preferences never seed another's runs.
- **`workflow_runs.user_id`** is now written at `register_run` from the run's
  owner (today it is hard-coded `None`). Every per-run table (`job_scores`,
  `review_rounds`, `tailored_resumes`, `llm_calls`, `run_metrics`, ...) inherits
  ownership transitively through its `workflow_run_id` foreign key — no schema
  change needed on those, only a join when the UI scopes reads.
- **`jobs`** stays a shared, unowned pool. Job postings are not user data;
  ownership is expressed by which run scored a job, so per-user history scoping
  falls out of the `workflow_runs` join.

`WorkflowState.user_id` (already in the schema, always `None` today) is populated
at kickoff and threaded into memory writes and the resume load.

### D. Per-user configuration as a two-layer merge

Because the requirement is to port **all** pre-existing data (including config
overrides) to user `0`, there is no separate "system-wide" override layer to
preserve. `user_config.user_id IS NULL` rows are migrated to `"0"` (Decision G),
and configuration collapses to a clean two layers:

```
config.yaml defaults  ->  user_config (user_id = X: per-user)
```

`ConfigService.get_effective_config(user_id)` merges that user's overrides over
the YAML defaults. A newly created profile (id >= 1) starts with no overrides and
therefore runs on pure YAML defaults until it sets its own. Protected keys
(`_PROTECTED_KEYS`) and the ADR-061 ceiling clamps apply to the merged result
exactly as today, and remain sourced from YAML, so they are shared by every
profile regardless.

**Per-agent model/provider overrides (ADR-053/058) are part of this per-user
layer** — they are just `agents.{name}.{provider,model}` keys stored under the
user's id. Because use is sequential (Context constraint 1), the runtime
application is a rebuild, not a partition: `reload_deps_and_graph` rebuilds the
agent registry from the *active user's* effective config on profile switch / run
kickoff. The per-run `effective_config` snapshot (ADR-058) continues to record
exactly what a given run used.

### E. Isolation is cooperative now, enforceable later

Without authentication, history isolation is a **read-scoping** concern, not an
authorization one: `db_reader` and the UI show the active user's data; the API
resolves a user for writes. We deliberately do **not** add hard ownership-
authorization checks (e.g. rejecting `GET /workflows/{id}` when the requester is
not the owner) in this ADR — that check is meaningful only once identity is
authenticated, and adding it now would be security theatre. The seam in Decision B
is precisely where that enforcement attaches when auth arrives. This is stated
plainly so a future reader does not mistake the profile selector for an access
control boundary.

### F. UI: profile selector + onboarding wizard

- A sidebar **profile selector** (dropdown over `GET /users`) sets
  `st.session_state["current_user_id"]`. Switching profiles re-scopes every read
  view and is the trigger for a config/agent rebuild for the newly active user.
- **"Add profile" is a guided onboarding wizard**, not a single field. It walks
  three steps, and each step persists to that data's existing home — the wizard
  introduces no new storage concept beyond the `users` row:
  1. **Identity** — display **name** (required) + an optional **note**.
     `POST /users` creates the row and returns the next integer id (>= 1).
  2. **Resume** — upload a resume for the new profile, run through the existing
     parse-and-store path scoped to the new `user_id`; it becomes that profile's
     active resume in the `resumes` table.
  3. **Default search criteria** — roles and locations, persisted as the
     profile's **per-user `user_config` defaults** (e.g. `search.roles`,
     `search.locations`) under the new `user_id`. The "Start New Run" form
     pre-fills from these for that profile. This step is skippable.
  Steps 2 and 3 are convenience bundling only; a profile created with just step 1
  is fully valid and can add a resume / criteria later through the normal
  surfaces.
- The "Start New Run" form's resume input changes from a free-text "Resume ID"
  box to a picker over the active user's resumes, and pre-fills roles/locations
  from the profile's saved search defaults (step 3).

### G. Migration: port all existing data to user `0`

A timestamped backup of `data/v2.db` is taken before the migration runs. `init_db`
is then extended (same additive `ALTER TABLE` + `try/except` pattern already used
for prior migrations) to:

1. Create the `users` table and seed the default user `id = 0`
   (name e.g. "Primary") if the table has no row with `id = 0`.
2. Add `user_id TEXT` to `resumes` and `memory_items`.
3. Backfill **all** pre-existing data to user `"0"`: assign every existing
   `resumes`, `memory_items`, and `workflow_runs` row (where `user_id IS NULL`),
   **and** every existing `user_config` row (where `user_id IS NULL`), to `"0"`.
   The current owner's history, active resume, learned memory, and config
   overrides all remain visible and active under profile `0`. New profiles created
   later (id >= 1) start empty.

The migration is idempotent (re-running `init_db` is a no-op — the seed is guarded
on `id = 0` and the backfill only touches `user_id IS NULL` rows) and additive (no
column drops, no data loss).

## Options considered

- **Profile selector with no auth, on a real `users` table + single identity seam
  (chosen).** Lowest near-term effort that still leaves the ceiling high. The
  expensive, hard-to-reverse work (the data model) is done once and is identical
  regardless of the eventual auth model; the cheap, swappable work (the front
  door) is isolated to one seam.
- **Full login with enforced isolation now.** Rejected for now as overkill for a
  trusted personal/family tool: it adds sessions, credential storage, and per-
  endpoint authorization for capacity not yet needed. Not foreclosed — Decision B
  and E exist specifically so it is additive later.
- **Config/CLI-driven user bundles (a named config+resume selected at kickoff, no
  UI).** Rejected as clunky to switch and lacking the add-user affordance the user
  asked for.
- **Per-user partitioning of the runtime (separate graph/deps/registry per user).**
  Rejected: unnecessary under sequential use. A rebuild-on-switch is simpler and
  the per-run `effective_config` snapshot already preserves what each run used.
- **Migrate existing data fresh / untagged.** Rejected: backfilling to a default
  user keeps the current owner's history coherent and avoids an untagged-memory
  pool that would bleed across future profiles.

## Consequences

### Positive

- Multiple people can be served from one installation, each with their own resume,
  search criteria, config overrides, model assignments, cost view, and history.
- Memory isolation removes cross-contamination of learned preferences between
  people — a correctness and privacy improvement.
- The identity seam means a future move to real authentication touches one backend
  function and the UI selector, not the data model or repositories.
- Backward compatible: existing data is preserved under profile `0`; a request
  with no `user_id` parameter resolves to user `0`, so nothing breaks before the
  UI is updated.

### Tradeoffs

- Every read path gains a `user_id` filter and the per-run tables gain a
  `workflow_runs` join; mitigated with indexes on the new `user_id` columns.
- A profile switch triggers an agent/registry rebuild (acceptable under sequential
  use; would need rethinking if concurrent runs are ever required).
- Isolation is cooperative, not enforced (Decision E). This is a deliberate,
  documented limit of the no-auth model, not an oversight.

### Neutral

- New `users` table and `user_id` columns to document in `data_model.md`; the
  two-layer per-user config merge in `config_model.md`; the identity query
  parameter and two new `/users` endpoints in `api_reference.md`; new persistence/identity
  invariants in `CLAUDE.md`; the per-user memory note in
  `state_and_memory_model.md`; and the cooperative-isolation note in
  `security.model.md`.

## References

- ADR-046 — Hybrid configuration model (YAML + DB overrides); this generalizes the
  override layer to per-user with a system-wide base.
- ADR-053 / ADR-058 — Per-agent provider/model selection; agent overrides become a
  per-user config layer applied by rebuild under sequential use.
- ADR-040 — Data retention and privacy policy; per-user isolation of resume and
  memory data.
- ADR-021 / ADR-023 / ADR-027 — Workflow-run persistence and observability/cost
  tracking, now attributable per user via the populated `workflow_runs.user_id`.
