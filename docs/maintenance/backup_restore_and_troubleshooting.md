# Backup, Restore, and Troubleshooting

> **Type:** How-to. **Part of:** [Maintainer Handbook](../maintenance.md).
>
> Operational recipes: back up and restore the data safely, and fix the symptoms a
> maintainer actually hits. For cost-specific diagnosis use
> [cost_troubleshooting.md](../cost_troubleshooting.md); for the concurrency model behind
> some of these symptoms see [persistence_and_concurrency.md](persistence_and_concurrency.md).

---

## What is state vs what is source

Only `data/` and `config/config.yaml` are local state; everything else is in git.

| Path | Back up? | Notes |
|---|---|---|
| `data/v2.db` (+ `-wal`, `-shm`) | **Yes** | The whole application DB *and* the LangGraph `checkpoints`. The single most important file. |
| `data/jobs.db` | Optional | Aux jobs store; regenerable by re-running discovery. |
| `data/linkedin_inbox.txt` | If used | Your LinkedIn URL inbox; auto-created empty otherwise. |
| `config/config.yaml` | **Yes** | Gitignored; your real (non-example) config. |
| `.env` | **Yes, securely** | Secrets. Never commit. Store in a secret manager, not the backup. |

---

## Backing up the database (WAL-aware)

SQLite in WAL mode keeps recent writes in the `-wal` sidecar until a checkpoint folds them
into the main file. A naive copy of only `v2.db` can miss in-flight data.

**Preferred (consistent, online-safe):** use SQLite's backup API / CLI, which produces a
single consistent file regardless of WAL state:

```bash
sqlite3 data/v2.db ".backup 'backups/v2-YYYY-MM-DD.db'"
```

**Acceptable when the app is stopped:** stop the backend (so no writer is active and WAL is
checkpointed on clean close), then copy `data/v2.db`. If you copy a *running* DB by hand,
copy `v2.db`, `v2.db-wal`, and `v2.db-shm` **together** — never `v2.db` alone.

Keep `config/config.yaml` alongside each DB snapshot so a restore is self-contained.

---

## Restoring

1. Stop the backend (and UI).
2. Replace `data/v2.db` with the backup (and remove any stale `v2.db-wal` / `v2.db-shm` so
   they don't shadow the restored file).
3. Restore `config/config.yaml` if needed.
4. Start the backend. `init_db()` runs at startup and is idempotent — it will apply any
   migrations the restored DB is missing (additive `ALTER`s), so an older backup is brought
   up to the current schema automatically. See
   [schema_and_migrations.md](schema_and_migrations.md).
5. Sanity check: `GET /readyz` returns `200` and the System Dashboard renders.

> A backup taken from an older code version restores cleanly because migrations are additive
> and idempotent. The reverse is not guaranteed — restoring a *newer* DB into *older* code
> can expose columns the old code doesn't know about. Match (or exceed) the code version.

---

## Troubleshooting

### Symptom: you restart the backend but the old behavior persists (stale code)

Most common on **Windows** after a Bash `&` background launch. An orphaned uvicorn — and
critically its **multiprocessing-fork child** — keeps holding port `8000` and serving the
**old** code, so new endpoints 404 while old ones still 200.

- The parent PID may be invisible to `Get-Process` / `taskkill /PID` even though the socket
  is held — because the *child* process (a `spawn_main parent_pid=<...>` python process)
  inherited the listening socket.
- **Fix:** find and kill the multiprocessing-fork **child**, not just the parent:

```powershell
# Find what is actually listening on 8000
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
# Inspect the owning process (and look for a python child with parent_pid=<orphan>)
Get-Process -Id <pid>
# Kill the child holding the socket
taskkill /F /PID <child_pid>
```

- **Prevent it:** start servers via PowerShell `Start-Process`, **not** a Bash `&` launch.
  See [running_and_operating.md](running_and_operating.md).

### Symptom: a run is stuck at `running` (or `cancelling`) forever

A process death mid-run can freeze the `workflow_runs` row while the checkpoint says
otherwise — the two state stores are not written atomically (see
[persistence_and_concurrency.md](persistence_and_concurrency.md)).

- **Normal recovery:** restart the backend. ADR-096 startup recovery
  (`recover_orphaned_runs`) re-submits checkpointed-but-unfinished runs (up to
  `MAX_RESUME_ATTEMPTS=3`), and `reconcile_orphaned_runs` is the fail-everything backstop.
- **If it persists after a restart:** the run exhausted its resume attempts or has no usable
  checkpoint; it will be reconciled to a terminal status. Check the run's `errors[]` and the
  `step_executions` timeline on the Workflow Detail page for where it died.

### Symptom: scores/results "disappear" or cost is re-spent on the same jobs

A persist failure may have been swallowed on an older code path, or a write lost contention.
The 2026-06-13 fixes surface paid-output persist failures to `errors[]` / a `persisted:false`
flag — **check the run's `errors[]` first.** If you see write contention, confirm WAL +
busy_timeout are active (they are set in `get_connection`). Background:
[persistence_and_concurrency.md](persistence_and_concurrency.md).

### Symptom: `GET /readyz` returns `503` or `degraded`

- `503` -> the **database** check failed (critical). Confirm `data/v2.db` exists and is
  writable; check disk space and that no stale process holds an exclusive lock.
- `degraded` (200) -> a non-critical capability is down: `agent_provider` (missing/invalid
  `ANTHROPIC_API_KEY`) or `adzuna` (missing creds). Workflows still run on what's available.
  `openai` is optional. The check reports presence/mode only — it never leaks key values.

### Symptom: config change didn't take effect

Match the change to its apply path (see the table in
[running_and_operating.md](running_and_operating.md)): search/scoring/scrapers apply next
run; agent model assignment needs `POST /config/reload`; **prompt file** changes and **code**
changes need a restart (the `PromptLoader` caches prompts at first read).

### Symptom: a cost surprise

Go straight to [cost_troubleshooting.md](../cost_troubleshooting.md) — per-agent cost
queries, reconciliation against the provider billing console, and the lever decision matrix.
Remember the run cost cap is a **soft governor**, not a hard ceiling
([persistence_and_concurrency.md](persistence_and_concurrency.md)).

---

## Escalation pointers

| Class of problem | Where it's recorded |
|---|---|
| A critical **runtime** bug (with a forcing-function test) | [bugs/README.md](../../bugs/README.md) |
| An operational **incident / postmortem** | [incidents/README.md](../incidents/README.md) |
| A structural weakness / known ceiling | [architecture/architecture_review_2026-06-13.md](../architecture/architecture_review_2026-06-13.md) + the roadmap in [the handbook](../maintenance.md#the-open-roadmap-what-is-deliberately-not-done) |
