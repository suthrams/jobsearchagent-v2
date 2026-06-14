# Schema and Migrations

> **Type:** How-to + reference. **Part of:** [Maintainer Handbook](../maintenance.md).
>
> How the database schema is defined and evolved, and the exact recipe for adding or
> changing a column safely. For the full per-column data dictionary see
> [architecture/data_model.md](../architecture/data_model.md); for the concurrency
> properties of the connection layer see
> [persistence_and_concurrency.md](persistence_and_concurrency.md).

---

## The databases

| File | What it holds |
|---|---|
| `data/v2.db` | The application database: all domain, observability, security, identity, and lifecycle tables, **plus** the LangGraph `checkpoints` table (the `SqliteSaver` writes here). |
| `data/jobs.db` | The legacy/aux jobs store from the shared v1 scraper libraries. |
| `data/linkedin_inbox.txt` | One LinkedIn job URL per line; the LinkedIn scraper's input (auto-created empty if missing). |

Both `data/` and `config/config.yaml` are **gitignored** — they are local state, not source.
The table inventory and column dictionary live in
[architecture/data_model.md](../architecture/data_model.md) (the authoritative count is
there; this doc does not duplicate it).

---

## How the schema is defined

Everything is in one module: `app/repositories/database.py`.

- **`_SCHEMA_SQL`** — a single `executescript` block of `CREATE TABLE IF NOT EXISTS` +
  `CREATE INDEX IF NOT EXISTS` statements. This is the baseline schema for a fresh DB.
- **`init_db(db_path)`** — runs `_SCHEMA_SQL`, then applies the **migration list** (next
  section). It is idempotent and safe to call repeatedly; `_build_real_deps` calls it at
  every startup.
- **`utcnow_iso()`** — the **only** sanctioned way to produce a timestamp anywhere in the
  system. Every table stores ISO-8601 UTC strings so string-sort ordering and purge queries
  are correct. Never generate a timestamp by any other means.
- **`get_connection()`** — the connection context manager (WAL + busy_timeout). All
  repositories go through it.

---

## How migrations work today (and the gap)

There is **no migration framework and no `schema_version` table.** Evolution is an
accumulating list of guarded `ALTER TABLE` statements in `init_db`, each in the same shape:

```python
# Migration (ADR-105): persist the Research Agent's per-job output alongside the score.
try:
    conn.execute("ALTER TABLE job_scores ADD COLUMN research_context_json TEXT")
except Exception:
    pass  # column already exists
```

The contract this relies on:

- `_SCHEMA_SQL` uses `IF NOT EXISTS`, so a fresh DB and an existing DB both end at the same
  shape.
- Each `ALTER` is wrapped so "column already exists" is a no-op on an already-migrated DB.
- New columns are **additive and nullable** (or `NOT NULL DEFAULT ...`), so existing rows
  remain valid.

**The known weakness (open roadmap item 5):** the `except Exception: pass` swallows *any*
failure, not only "column exists." A failure for some other reason leaves a silently
inconsistent schema with no audit trail. This is `Low` risk at the current size and `Medium`
as the list grows. The planned fix is a `schema_version` table that records which migrations
have run, so failures are detectable and the list can be pruned. **Do not** "fix" this by
making the `except` narrower in isolation — it needs the version table to be a real fix; raise
it as the roadmap item if it bites.

---

## How to add or change a column (the recipe)

Follow this in order — it touches more than just `database.py`.

1. **Decide it belongs in SQL, not state.** Workflow-run state that only matters during a
   run lives in `WorkflowState`; persisted facts the UI/history reads live in a table.
2. **Add the column to `_SCHEMA_SQL`** so fresh databases get it from the `CREATE TABLE`.
3. **Add a guarded `ALTER` to the `init_db` migration list** so existing databases get it
   too. Match the established shape exactly (comment citing the ADR, additive + nullable or
   `NOT NULL DEFAULT`, `try/except: pass`). If a new index references a column added by
   `ALTER`, create that index **after** the `ALTER`, not inside `_SCHEMA_SQL` (the script
   runs before the migrations — see the note near the index block in `database.py`).
4. **Update the schema in the affected Pydantic model** (`app/schemas/`) so the value is
   typed end-to-end. If the parser/agent produces it, it must be in the schema or it can't
   be stored or recovered.
5. **Update the repository** (`app/repositories/<table>_repository.py`) read/write methods,
   and any affected read-model in `app/services/reads/`.
6. **If the UI reads it,** thread it through the API (`app/api/routers/`) and `api_client` —
   the UI never reads the DB directly (ADR-075).
7. **Update the docs:** [architecture/data_model.md](../architecture/data_model.md) (the data
   dictionary) and run the architecture-docs sweep ([CLAUDE.md](../../CLAUDE.md)).
8. **Test:** add coverage; run the suite ([testing.md](../testing.md)).

Examples to copy from (all in `database.py::init_db`): ADR-105 (`research_context_json` on
`job_scores`), ADR-080 (`posted_at` on `jobs`), ADR-057 (per-job exclusion columns), ADR-100
(favorites kind/url/source), the cost-tracking cache-token columns on `llm_calls`.

### Constraints and backfills

Adding a `UNIQUE` constraint to an existing table is more than an `ALTER ADD COLUMN` — SQLite
won't add a table constraint in place, and existing rows may violate it. The 2026-06-13 fix 2
(`UNIQUE(workflow_run_id, job_id)` on `job_scores`) used a **dedupe-safe migration**: it
removes pre-existing duplicate rows before establishing the constraint. Follow that pattern
for any constraint that could be violated by historical data, and pair the write with
`INSERT OR IGNORE` / `INSERT OR REPLACE` as appropriate.

---

## Retention and purge

Data removal is **explicit-trigger-only** (ADR-070) — `purge_old_data()` never runs
automatically. Fire it via `POST /admin/purge`, `tools/purge_data.py`, or the Settings
control. A purged run cascades to all its child rows; a resume's purge runs on a separate
window. The `idempotency_keys` table (ADR-082) is **not yet** in the purge cascade — note
that if you wire retention end-to-end. See `data_model.md` Section 8A.

---

## Quick reference

| Need | Where |
|---|---|
| Baseline schema | `database.py` `_SCHEMA_SQL` |
| Apply schema + migrations | `database.py::init_db` (idempotent; called at startup) |
| Add a column | `_SCHEMA_SQL` **and** the `init_db` migration list (both) |
| Timestamps | `database.py::utcnow_iso()` — the only source |
| Column dictionary | [architecture/data_model.md](../architecture/data_model.md) |
| Why no migration framework | open roadmap item 5 (`schema_version` table planned) |
| Purge / retention | `POST /admin/purge` / `tools/purge_data.py` (ADR-070) |
