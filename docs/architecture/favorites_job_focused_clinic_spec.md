# Spec: My Favorite Jobs + Job-Focused Resume Clinic

Companion to [ADR-090](adr/ADR-090-favorites-and-job-focused-clinic.md). This is the
artifact to review **before** any code is written. Status: **Proposed — awaiting
sign-off.**

---

## 1. Objective

Let a job seeker **flag a few jobs as "My favorite jobs"** and then **focus a Resume
Clinic session on one of them** to produce a **tailored, exportable resume** for that
specific role. Today the Resume Clinic is job-agnostic; this gives it an optional
per-job focus without splitting the tailoring flow across screens.

**User stories**
- As a job seeker scanning **Matches**, I can favorite (and un-favorite) a role so I
  can come back to it.
- As a job seeker on the **Opportunity** page, I can favorite that role.
- In the **Resume Clinic**, I can optionally pick one of My favorite jobs to focus
  the session; the output is a resume tailored to that job, which I can export. With
  no job picked, the clinic behaves exactly as today.

**Success looks like:** the favorite -> focus -> tailor -> export loop works
end-to-end, per profile, with the favorites set bounded and carrying **no application
status** of any kind.

### Hard product boundary (the reason this needs an ADR)

"Favorites" must be a **filter-input working set** — a signal the user gives the
system to tailor toward — **not application tracking**. It carries only a job
reference + a display snapshot + a timestamp. It MUST NOT grow
`apply / applied / status / pursuing / stage / saved-as-outcome` fields. The career
decision point stays human-owned (CLAUDE.md "No application tracking"; the
filter-vs-tracker distinction; ADR-088 §E). This is the one rule the feature is
consciously threading, so the ADR documents it and a guardrail test enforces it.

---

## 2. Tech Stack & Commands

Unchanged from the project baseline (CLAUDE.md): FastAPI + Uvicorn, SQLite (raw
`sqlite3`), Pydantic v2, Streamlit native-multipage UI (ADR-088/089), pytest.

```bash
python -m pytest tests/                 # full suite (mock mode; must stay green)
python -m pytest tests/ -m integration  # live-API smoke (gated)
python .claude/skills/smoke-test-ui/smoke_ui.py   # headless UI render check
bash tools/check_no_secrets.sh          # secret audit (pre-commit + pre-push)
uvicorn app.api.main:app --reload       # backend
streamlit run app/ui/streamlit_app.py   # UI
```

---

## 3. Design

### 3.1 Data model — `favorite_jobs` (new table)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `user_id` | TEXT NOT NULL | Decimal-string profile id (ADR-062). |
| `workflow_id` | TEXT NOT NULL | The run that surfaced the job (for JD resolution). |
| `job_id` | TEXT NOT NULL | The scored job. |
| `title` | TEXT | Snapshot at favorite time (survives run purge). |
| `company` | TEXT | Snapshot. |
| `created_at` | TEXT NOT NULL | ISO-8601. |

- **UNIQUE(`user_id`, `job_id`)** — a job is favorited at most once per profile;
  POST is idempotent on a duplicate.
- **No status/notes/outcome columns — ever** (the boundary). A schema-level forcing
  function: a test asserts the column set is exactly the above.
- **Cap:** `MAX_FAVORITES = 25` per profile (a working set, not a board). The 26th
  POST is rejected `409 favorites_cap_reached`.
- **Retention (ADR-070):** favorites are **user-owned working data**, deliberately
  **NOT** cascaded when their source *run* is purged — the title/company snapshot
  persists so the dropdown still shows the role (JD resolution then degrades
  gracefully). Deleting a *profile* DOES remove its favorites. Documented exception
  to the run-purge cascade.

### 3.2 Repository — `app/repositories/favorite_repository.py`

```python
class FavoriteRepository:
    def add(self, user_id: str, workflow_id: str, job_id: str,
            title: str | None, company: str | None) -> dict: ...   # enforces cap + UNIQUE
    def remove(self, user_id: str, job_id: str) -> None: ...
    def list_for_user(self, user_id: str) -> list[dict]: ...        # newest first
    def favorited_job_ids(self, user_id: str) -> set[str]: ...      # batch lookup for the UI
    def count_for_user(self, user_id: str) -> int: ...
```

`add` raises a typed `FavoritesCapReached` when `count_for_user >= MAX_FAVORITES`.

### 3.3 API — per-profile, path-scoped (the `/users/{id}/...` family)

Favorites join the existing per-user resource family (`/users/{id}/resume`,
`/users/{id}/resume-clinic`), so they use the **path** `{user_id}` (the ADR-066
documented exception to the ADR-062 `?user_id=` seam).

| Method | Path | Body / Result |
|---|---|---|
| `GET` | `/users/{user_id}/favorites` | `{ "favorites": [ {job_id, workflow_id, title, company, created_at} ] }` |
| `POST` | `/users/{user_id}/favorites` | `{workflow_id, job_id}` -> server snapshots title/company from that run's scored jobs; `201`; `404 job_not_found`; `409 favorites_cap_reached` |
| `DELETE` | `/users/{user_id}/favorites/{job_id}` | `204` (idempotent) |

Reads flow through the API only (ADR-075). `api_client.py` gains
`list_favorites / add_favorite / remove_favorite`; `data.py` a `_cached_favorites`
wrapper.

### 3.4 The job-focused clinic — REUSE, don't rebuild

The clinic's job focus **routes to the existing tailoring path**, it does not add a
new agent:

- **No focus** -> today's `run_clinic(...)` with `ResumeReviewerAgent` (job-agnostic).
- **Focus = a favorited job** -> the UI renders the existing tailoring flow for that
  job's `(workflow_id, job_id)`: `POST /workflows/{wf}/jobs/{job}/tailorings`
  (Tailoring Agent + Fidelity Reviewer, ADR-059) -> drafts list + approve / revise /
  reject / edit decisions -> ADR-072 live chat -> multi-format export. These are the
  **same** endpoints and components the Opportunity page uses; the clinic is a
  resume-first entry point into them, with the job picked from favorites.

So the backend net-new is **only the favorites table/repo/endpoints**. The clinic
focus is UI composition over existing tailoring endpoints + the favorites selector.
The JD comes from the job's `job_description` in the run state (already present); if
the run was purged, the focus selector flags the job as unavailable and offers a
re-pick / the job-agnostic clinic.

### 3.5 UI touchpoints

- **Matches** (Roles tab): on the selected-row action cluster (which already has
  *Open opportunity* / *Exclude*, since `st.dataframe` can't host in-row buttons —
  ADR-088 R-2), add **⭐ Favorite / ★ Un-favorite** (label reflects current state).
  Optionally a small "🌟" marker column for already-favorited rows.
- **Opportunity** page header: a **⭐ Favorite** toggle next to *Hide*.
- **Resume Clinic**: an optional **"Focus a job (from My favorite jobs)"** selectbox
  at the top. Empty -> clinic unchanged. A pick -> render the tailoring panel
  (`components/tailoring` + the ADR-072 chat) for that job.
- A shared `app/ui/components/favorites.py` renders the toggle so Matches and
  Opportunity stay consistent.

---

## 4. Project Structure (where the new code lives)

```
app/repositories/favorite_repository.py     NEW  - FavoriteRepository (+ MAX_FAVORITES)
app/schemas/favorite.py                      NEW  - FavoriteJob pydantic model
app/api/routers/users.py (or favorites.py)   EDIT - the 3 endpoints
app/services/reads/...                       EDIT - favorites read (if a read svc is used)
app/ui/components/favorites.py               NEW  - the shared ⭐ toggle
app/ui/views/matches.py                      EDIT - favorite on selected row
app/ui/views/opportunity.py                  EDIT - favorite toggle in header
app/ui/views/resume_clinic.py                EDIT - optional focus dropdown -> tailoring
app/ui/api_client.py / data.py               EDIT - favorites calls + cached read
data/v2.db schema bootstrap                  EDIT - create favorite_jobs
docs/architecture/data_model.md              EDIT - the new table + retention note
docs/architecture/adr/ADR-090-...md          NEW
```

---

## 5. Code Style

Match the surrounding code. Repository example (mirrors existing repos — raw sqlite3,
dict rows, typed errors):

```python
class FavoritesCapReached(Exception):
    """Raised when a profile already has MAX_FAVORITES favorites."""


class FavoriteRepository:
    def add(self, user_id, workflow_id, job_id, title, company):
        if self.count_for_user(user_id) >= MAX_FAVORITES:
            # idempotent: a re-favorite of an existing job is fine, only NEW ones cap
            if job_id not in self.favorited_job_ids(user_id):
                raise FavoritesCapReached(MAX_FAVORITES)
        # INSERT OR IGNORE on UNIQUE(user_id, job_id) ...
```

---

## 6. Testing Strategy

- **Repository unit tests:** add/remove/list/count, UNIQUE idempotency, the 25-cap
  (26th raises), per-profile isolation.
- **Schema forcing function** (CLAUDE.md "a forcing-function test per newly-wired
  table"): assert `favorite_jobs` columns are exactly the spec'd set — fails the
  build if a `status`/`applied`/outcome column is ever added (the boundary, enforced
  in schema).
- **API tests:** POST snapshots title/company; cap -> `409`; DELETE idempotent; GET
  scoped to the profile; `404` for an unknown job.
- **No-tracking guardrail (extended):** the existing `test_ui_structure` scan extends
  to `components/favorites.py` + `views/resume_clinic.py`, still forbidding
  `apply/applied/status/pursuing/stage/" save "`; "favorite" is the sanctioned word.
- **UI:** `smoke-test-ui` renders Matches/Opportunity/Resume-Clinic with the new
  controls; a test that the clinic focus selector is optional (empty -> reviewer path)
  and that a focus renders the tailoring panel.
- **ADR-075 invariant** (UI never opens the DB) must stay green — favorites read via
  the API.

---

## 7. Boundaries

- **Always:** run `pytest` before commit; reads via the API (ADR-075); redact PII at
  the LLM seam (unchanged — tailoring already does, ADR-069); secret audit each
  commit; favorites carry only job-ref + snapshot + timestamp.
- **Ask first:** any change to the favorites column set; raising `MAX_FAVORITES`;
  adding a dedicated Favorites nav screen; making the clinic focus also re-point the
  scorecard (job-aware reviewer) rather than only tailoring.
- **Never:** add `apply/applied/status/pursuing/stage/outcome` to favorites or any
  job surface; let favorites become an application tracker; bypass the cap; open the
  DB from the UI.

---

## 8. Success Criteria (testable)

1. Favorite/un-favorite works from **Matches** (selected-row) and **Opportunity**;
   state persists per profile and survives reload.
2. The favorites set never exceeds **25**; the 26th is rejected with a clear message.
3. Favorites survive a retention purge of their **source run** (snapshot persists);
   deleting a **profile** removes its favorites.
4. The Resume Clinic shows an **optional** "Focus a job" dropdown from My favorite
   jobs; with none selected the clinic is byte-for-byte today's behavior.
5. Selecting a focus job produces a **tailored resume draft** for that job, with the
   same approve/revise/reject/edit + live chat + export as the Opportunity page.
6. **No** favorites/clinic surface exposes apply/applied/status/pursuing/stage; the
   guardrail test passes; the schema forcing-function test passes.
7. Full suite green; UI smoke green; ADR-075 + PII invariants still pass.

---

## 9. Open Questions

- **O-1.** Favoriting is allowed for **any scored job** (not only those above
  threshold) — assumed yes (you may want to tailor toward a slightly-lower-fit role).
- **O-2.** The clinic focus produces a **tailored resume only**; it does **not** also
  re-run the job-agnostic scorecard against the job (kept out of scope to avoid
  splitting two analyses). Possible future enhancement.
- **O-3.** No dedicated **Favorites** nav screen in v1 (favorites surface as the
  toggle + the clinic dropdown). Add later if wanted.
