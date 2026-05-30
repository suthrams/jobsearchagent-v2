# ADR-070: Data Retention and State De-duplication (At-Rest Phase 1)

## Status

Accepted (2026-05-30). Design ratified; implementation pending.

Phase 1 of the data-at-rest track. Implements the long-accepted but never-built
[ADR-040](ADR-040-define-data-retention-and-privacy-policy.md) ("store user data
only as long as necessary; avoid indefinite raw resume storage; support
deletion"). Follows the send-side fix
[ADR-069](ADR-069-redact-direct-identifiers-at-the-llm-seam.md) and the options
analysis in [`spike_data_at_rest_security.md`](../spike_data_at_rest_security.md)
(this ADR ratifies that spike's **Option A**). Encryption at rest (the spike's
Option B) is explicitly deferred to a separate Phase 2 ADR.

## Context

ADR-069 closed the *send-side* PII gap (what we transmit to model providers).
What remains is the *at-rest* gap traced in `pii_data_flow.md`: every resume,
parsed profile, workflow state, and agent output sits in **plaintext** in
`data/v2.db`, **indefinitely**. The spike ranked the threats for this deployment
(single-user, Windows 11 Pro, BitLocker assumed on) and found two at-rest gaps
that retention + de-duplication address directly without any new dependency:

- **B2 - retention is unwired and incomplete.** `purge_old_data()`
  (`app/repositories/database.py:381`) takes configurable windows and is
  unit-tested, but:
  - it is **never called** anywhere in app code (no endpoint, CLI, or startup
    hook), so nothing is ever deleted;
  - it **skips the PII-heaviest tables** - `resumes`, `tailored_resumes`,
    `resume_clinic_reviews`;
  - it does **not cascade** from `workflow_runs` to its child rows, so purging a
    run on its own window would orphan `job_scores`, `career_advice`,
    `interview_prep`, `tailored_resumes`, `resume_clinic_reviews`, `review_rounds`,
    and `human_decisions`.

- **B3 - `state_json` duplicates the full un-redacted profile.** `load_resume.py`
  writes `profile.model_dump()` into `state["resume_profile"]` (lines 44, 69),
  which the orchestrator serializes into `workflow_runs.state_json` **and** the
  LangGraph `checkpoints` blob. That copy still contains `raw_text`, `name`,
  `email`, `location`, and `file_name` - the single largest aggregate of PII per
  run, stored in two more places than the source resume row. ADR-069 redacts only
  at the *egress to agents*, not in state.

**Why retention first, before encryption (the spike's reasoning):** retention is
the highest value per unit of risk. It needs no new deps, has none of the
SqliteSaver / Windows-compiled-dependency friction the encryption options carry,
and *shrinks the very surface every later encryption option has to protect*. The
B3 dedup additionally keeps full PII out of `state_json` and the checkpoints blob
- which app-level field encryption (Phase 2 Option B) cannot easily reach anyway.

### What the architecture allows (grounding the decision)

- Every node reads `state["resume_profile"]` and re-redacts it **at the LLM seam**
  via `trim_resume_profile` / `redact_pii_for_llm` (`score_jobs.py:110`,
  `deep_review_runner.py:66`, `career_advice.py:63`, `interview_prep.py:68`). No
  agent consumes the un-redacted state copy.
- The only non-agent read of the state copy is the Streamlit tailoring render
  (`streamlit_app.py:1616` -> `_section_order` / `_section_display`), which uses
  **section structure** (experience / education / skills labels), not the direct
  identifiers.
- The deterministic resume renderer
  (`resume_text_renderer.py::compose_resume`) reads name / email / location from
  the **stored resume row** (`resumes.parsed_profile_json`), **not** from
  `state_json`. Minimizing the state copy therefore does not touch the renderer's
  source of truth. This is the clean distinction from ADR-069, which rejected
  redacting the *stored resume row* (the renderer's source of truth) - we are
  minimizing only the derived state *copy*, not the source.
- `resumes` carries `is_active`, `version`, `raw_text_hash`, `created_at`; a
  resume is cache-keyed by `raw_text_hash` and can back multiple runs - so a
  resume purge must not delete a row still referenced by a non-purged run.

## Decision

Two changes, both deterministic, no new dependency.

### A. Complete and wire `purge_old_data()` (retention)

**A1 - Extend coverage to the PII tables with cascade.** Add the per-run PII
tables to the purge, deleting them by `workflow_run_id` join when their parent
`workflow_runs` row is purged, in one transaction:

- Cascade children of a purged run: `job_scores`, `career_advice`,
  `interview_prep`, `tailored_resumes`, `resume_clinic_reviews`, `review_rounds`,
  `human_decisions` (plus the observability tables `step_executions`,
  `agent_events`, `llm_calls` already in the plan).
- The observability tables keep their **independent, shorter** windows
  (`observability_days`, default 30) so cost data can be swept earlier than the
  run; the cascade is an additional sweep that catches any child still present
  when its parent run is deleted. Order matters: delete children first, then the
  parent `workflow_runs` row, to keep the DB referentially clean at every step.

**A2 - Resumes purged on their own, longer window, with a reference guard.**
The resume row is user-owned and longer-lived, so it is **not** cascaded from a
run. Instead:

- Never delete an **active** resume (`is_active = 1`), regardless of age - it is
  the user's current resume.
- Delete an **inactive** (superseded) resume only when it is both older than
  `resumes_days` (default 365) **and not referenced by any non-purged
  `workflow_run`** (the `raw_text_hash` cache-key caveat: one resume can back
  many runs). The reference check is the gate, not the age alone.

This answers the spike's Q1: deleting an old run deletes its tailorings / reviews
/ prep / advice (cascade), while the resume row survives on its own clock.

**A3 - Explicit trigger, never automatic.** Preserve the existing docstring
contract ("Purge is explicit - never runs automatically"). Expose the trigger as:

- a **manual admin endpoint** `POST /admin/purge` (returns the
  `{table: rows_deleted}` map, identity via the ADR-062 seam),
- a **CLI / script entry point** (`tools/purge_data.py`, confirm-by-default) for
  headless runs, and
- a **guarded Streamlit control** on the Settings page (a confirm checkbox gates
  the run button; the `{table: rows_deleted}` result is shown). The UI calls the
  endpoint - it adds no new server logic.

No scheduler infrastructure exists today and none is added; an opt-in
startup-sweep flag is named as a future extension, not built here. Windows are
read from config (`retention.*`, per ADR-040), defaults unchanged where they
exist (`workflow_runs_days=90`, `observability_days=30`, `security_events_days=180`,
`memory_items_days=365`, `jobs_days=90`) plus the new `resumes_days=365`.

### B. De-duplicate the profile out of `state_json` (the B3 dedup)

At the `load_resume.py` write sites, store the **redacted profile**
(`redact_pii_for_llm(profile.model_dump())`, the ADR-069 shape) into
`state["resume_profile"]` instead of the full `model_dump()`. Consequences:

- `raw_text`, `name`, `email`, `location`, and `file_name` never enter
  `workflow_runs.state_json` **or** the LangGraph `checkpoints` blob. The only
  un-redacted copy is the source `resumes` row, which retention (A2) bounds.
- Agents are unaffected: they already re-redact at the seam, and redaction is
  idempotent.
- The Streamlit render-from-state path is unaffected: it reads section structure,
  which is **kept** by `redact_pii_for_llm`.
- The deterministic renderer is unaffected: it reads the stored resume row, not
  state.

**Chosen over** the alternative dedup of storing only `resume_id` + a minimal
read-back field set (spike Q2): the redacted-profile approach keeps the exact
dict *shape* every read-back site already expects, so no node, the checkpointer's
resumption, or `db_reader`'s run-metadata extraction has to change. It is the
minimal-surface, lowest-risk form of the dedup.

### Out of scope (deferred to Phase 2 / later)

- **Encryption at rest** (`raw_text`, the remaining `state_json`, the checkpoints
  blob) - the spike's Option B/C, a separate higher-effort ADR.
- **The LangGraph `checkpoints` table's own retention.** The dedup (B) removes the
  profile from new checkpoints; bounding the checkpoint table's lifetime is a
  follow-on (it is resumption-only and already short-lived per run).
- **Relocating run-metadata out of `state_json` into dedicated plaintext columns**
  (the spike's L4) - only needed if Phase 2 encrypts the residual `state_json`;
  not required for retention or for the B3 dedup.

## Options considered

- **Retention + dedup, no encryption (chosen - spike Option A).** Highest value
  per unit of risk, no new deps, no SqliteSaver/Windows friction, shrinks the
  surface for every later option. Residual: data inside the window stays
  plaintext (accepted; BitLocker covers device theft, Phase 2 covers file-leak).
- **Field encryption first (spike Option B), before retention.** Rejected as
  Phase 1: encrypting a pile that grows forever is weaker than first bounding the
  pile, and doing the dedup first removes the hardest part of B (the
  `state_json` / L4 surgery). Deferred to Phase 2.
- **SQLCipher whole-DB (spike Option C).** Rejected for now: L2 (the shared-file
  SqliteSaver opens `data/v2.db` with stock sqlite3, no key), L3 (Windows
  compiled dependency), and whole-DB brick-on-key-mismatch risk are a lot of cost
  for a marginal gain over B once the dedup shrinks `state_json`. Reserved for a
  hosted / multi-user future.
- **Single retention window for all tables, including resumes.** Rejected:
  deletes the user's own (active) resume on the same clock as transient run data.
  The split window + active-guard keeps user-owned data while still bounding the
  transient PII.
- **Automatic startup / scheduled purge.** Rejected as the default: purge is
  destructive and irreversible; keeping it explicit (endpoint + CLI) matches the
  existing contract and the "gate the irreversible" stance (ADR-059). Opt-in
  startup sweep remains a future extension.

## Consequences

### Positive

- ADR-040 finally implemented: PII no longer accumulates indefinitely; the blast
  radius of any future file leak (T1/T5) and of device theft (T4) is bounded.
- The B3 dedup removes the largest per-run PII aggregate from two extra at-rest
  locations (`state_json` + checkpoints) that even Phase 2 field encryption could
  not easily reach.
- No new dependency, no SqliteSaver/Windows friction; pure-Python, reversible
  change.
- Cascade keeps the DB referentially clean (no orphaned child rows after a run is
  purged), which the previous partial purge did not.

### Tradeoffs

- Purge is destructive and irreversible by design; it is gated behind an explicit
  trigger and must be covered by tests (cascade completeness, resume reference
  guard, active-resume survival).
- Data inside the retention window is still plaintext at rest (Phase 2's job).
- One more transformation at the `load_resume` write site; the redacted-profile
  shape must stay a superset of what every read-back site needs (covered by
  existing read-back tests plus a new state-redaction assertion).

### Neutral

- **Docs:** this ADR + ADR-000 index; `state_and_memory_model.md` (state now holds
  the redacted profile; retention semantics); `data_model.md` (retention windows +
  cascade map; resume reference guard); `api_reference.md` (`POST /admin/purge`);
  `security.model.md` (at-rest posture: Phase 1 done, Phase 2 deferred);
  `pii_data_flow.md` (flip findings B2/B3 once implemented); `CLAUDE.md`
  persistence rules (retention is explicit; state stores the redacted profile);
  the spike's decision log.
- **Tests:** purge cascade (child rows gone with parent), resume reference guard
  (in-window-referenced resume survives; orphaned inactive resume past window is
  deleted; active resume always survives), and a state-dedup assertion
  (`state_json` after `load_resume` carries no `raw_text` / direct identifiers).
- **CLAUDE.md "Key Invariants":** add that retention is explicit-trigger-only and
  that `state["resume_profile"]` is stored redacted (the un-redacted profile lives
  only in the `resumes` row).

## Open questions resolved here

From the spike's Section 7:

1. **Retention windows / cascade** - resolved in A1/A2: per-run children cascade
   on the run's window; resumes use a separate `resumes_days=365` window guarded
   by active-flag and run-reference.
2. **What replaces the profile in `state_json`** - resolved in B: the redacted
   profile (ADR-069 shape), not a `resume_id` reference.
3. **Trigger for purge** - resolved in A3: manual endpoint + CLI; no scheduler;
   startup sweep deferred.
4. **Resume cache-key caveat** - resolved in A2: the run-reference guard prevents
   deleting a resume still backing a non-purged run.
5. **Phase 2 key management** - out of scope; deferred to the Phase 2 ADR.

## References

- ADR-040 - Data Retention and Privacy Policy (the policy this implements).
- ADR-069 - Redact Direct Identifiers at the LLM Seam (the send-side sibling; the
  dedup reuses its `redact_pii_for_llm` shape and respects its "source resume row
  stays un-redacted" stance).
- ADR-059 - Gate the irreversible (why purge stays explicit-trigger-only).
- `spike_data_at_rest_security.md` - the options analysis this ADR ratifies
  (Option A), and the threat model (T1-T5) and limits (L1-L5) it rests on.
- `pii_data_flow.md` - findings B2 (retention) and B3 (state duplication).
