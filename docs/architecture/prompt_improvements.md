# Prompt Improvement Backlog + Measurement Plan

Point-in-time critical review of all 14 prompts (`app/prompts/shared/guardrails.txt`
+ 13 agent prompts), 2026-06-12. Design contract lives in
[prompt_and_guardrails_model.md](prompt_and_guardrails_model.md); this doc is the
**why / before / after / how-to-measure** for the improvements that review found.

> **Status: all groups implemented 2026-06-12.** Group A defects are recorded in
> `bugs/BUG-014`. Each behavior change bumped its prompt `# version:` (and the
> shared guardrails is now versioned, surfaced as the `+g{N}` suffix in the logged
> `prompt_version` (log-only today — see the caveat below). Forcing tests:
> `test_bug014_prompt_schema_alignment.py` (A), `test_prompt_field_agnostic.py` (B).
> The measurement plan below stands as the way to evaluate impact going forward.

## How we measure a prompt change at all

Prompt edits are observable through telemetry we already wire — no new
instrumentation needed:

- **Schema-repair rate** (`agent_events.event_type='schema_repaired'`, ADR-078) —
  surfaced per-run via `system_health.performance_summary` -> "Schema repairs" on
  the System Dashboard. A prompt that fights its schema forces repairs; a fix
  should drop this toward 0 for that agent. **The primary correctness signal.**
- **Output tokens + cost** (`llm_calls.tokens_output`, `estimated_cost`) — brevity
  / structure changes move output tokens; read per-agent from `llm_calls`.
- **Prompt version tag** (`# version: N`, surfaced by `PromptLoader.get_version`
  as e.g. `scoring_agent:v3+g1`) — bump on every behavior change to mark the
  before/after boundary. **CAVEAT (verified 2026-06-12):** the version is emitted
  to the application LOG only (`provider ... prompt_version=...`); it is NOT a
  column on `llm_calls` or `agent_events` today, despite the aspirational note in
  observability.md / data_model.md. So version-slicing means grepping the run
  logs, not a SQL query. Persisting `prompt_version` to `llm_calls` is a small,
  worthwhile follow-up if DB-level slicing is wanted.
- **Verdict / behavior mix** (`discovery_stats.relevance_drops`,
  `scored_jobs` scores) — for filter/scoring changes, compare the distribution
  before/after (e.g. `too_senior` count, source mix).
- **Qualitative before/after validation run** — the profile-1 method used this
  session (runs `db64041b` -> `64c2c065`): same kickoff, diff the discovery stats
  and the scored set. Best for changes whose value is judgment quality, not a
  counter.

Default protocol for any prompt change below: bump the version, run the relevant
unit/forcing test, then a before/after validation run on a representative profile,
and read schema-repairs + output tokens for the affected agent.

---

## 1. Fidelity Reviewer — delete the stale output block (DEFECT)

- **Why:** `fidelity_reviewer.txt` (v5) describes TWO output schemas. Lines 40-44
  tell the model to "Produce: `passed_suggestions` / `failed_suggestions` /
  `overall_verdict` (approved/rejected)" — **none of these exist** in the
  `FidelityReview` schema (`overall_fidelity_status` pass/fail/needs_revision,
  `approval_recommendation` approve/revise/reject, `required_revisions`,
  `required_removals`, `unsupported_claims`, ...). The rest of the prompt targets
  the real schema. This is the system's final safety gate, so the cost of model
  confusion here is highest.
- **Before:** prompt instructs a non-existent shape; the model can emit
  `passed_suggestions`/`overall_verdict`, which fail validation and trigger a
  schema-repair pass (extra latency + tokens) or a degraded verdict.
- **After:** lines 40-44 removed; the prompt describes only the real
  `FidelityReview` fields it already covers later (required_revisions, etc.).
- **Measure:** schema-repair rate for `fidelity_reviewer` (expect a drop toward 0);
  fidelity output tokens (slight drop); spot-check that `overall_fidelity_status` /
  `approval_recommendation` are populated correctly on a tailoring run. Version v5
  -> v6 so the before/after is sliceable.

## 2. Review Auditor — resolve the contradiction + add field guidance (DEFECT)

- **Why:** `review_auditor.txt` (v1, least-maintained) tells the auditor to catch
  "overlooked gaps the critic missed" (line 24) yet "do NOT introduce new gaps
  that weren't in the critic's output" (line 29) — mutually exclusive. It also
  lacks the field list + "populate every field, `[]` not missing" guidance every
  other structured agent has, so it is the highest schema-repair risk in the suite.
- **Before:** contradictory mandate; the auditor either over-reaches (adds gaps,
  breaking its "annotate the critique, don't rewrite it" role) or suppresses a real
  miss. No empty-list guidance -> omitted-field repairs.
- **After:** reconcile to "surface a missed gap as an AUDIT FINDING (annotation),
  never by editing the critique"; add the `ReviewAudit` field list with the
  standard empty-list rule.
- **Measure:** schema-repair rate for `review_auditor` (expect drop); manual read
  of a few audits for whether missed gaps now appear as findings (not as injected
  critique gaps). Version v1 -> v2.

## 3. Field-agnostic example sweep (PRINCIPLE: profile-specifics in data, not prompts)

- **Why:** the relevance filter was deliberately made field-agnostic (v3/v4:
  "illustrative only", "do not assume any particular industry"). The SAME treatment
  was never applied to the other prompts carrying field-specific examples:
  `resume_parser` (cyber skill groups), `resume_reviewer` (cyber headline),
  `resume_chat` (cyber asides), `tailoring_agent` (software-eng examples). This
  contradicts the standing principle and biases tone/judgment toward tech/cyber for
  a non-tech candidate.
- **Before:** examples silently assume a tech/cyber candidate; a nurse or finance
  profile sees only out-of-field illustrations.
- **After:** each example block labelled "illustrative only — derive the equivalent
  for THIS candidate's field", and/or a second non-tech example added. Pin with the
  existing field-agnostic forcing-test pattern (`do not assume` / `different
  profession` / illustrative-only).
- **Measure:** hard to counter-measure directly; use a before/after validation run
  on a NON-tech synthetic profile and inspect whether outputs stop leaning tech.
  The forcing test prevents regression. Bump each touched prompt's version.

## 4. Version the shared guardrails + reconcile OUTPUT RULES with structured output

- **Why (versioning):** every agent prompt has `# version: N` (recorded on
  `llm_calls`), but `guardrails.txt` — prepended to EVERY agent — has none. A
  guardrails edit is therefore invisible to telemetry and unsliceable.
- **Why (OUTPUT RULES):** "Return only valid JSON", "no markdown fences", and the
  `{"error": "..."}` escape predate structured-output enforcement. The provider now
  forces the schema; most schemas cannot represent `{"error"}`, so that instruction
  is dead or misleading.
- **Before:** unversioned shared block; an error-escape instruction the schema
  layer won't honor.
- **After:** add `# version:` to guardrails; either make the error escape real
  (an optional error field where it matters) or update the rule to reflect that the
  schema is provider-enforced.
- **Measure:** version tag now appears wherever guardrails changes ship; no
  behavior counter, but the version makes any future guardrails A/B sliceable in
  `llm_calls`.

## 5. Scoring Agent — acknowledge truncated job descriptions

- **Why:** the whole ADR-104 thread showed Adzuna JDs are 500-char snippets.
  `scoring_agent.txt` scores the candidate against the JD with no note that the JD
  may be truncated, so a brief JD can understate fit (the inverse of the relevance
  filter's over-experienced leak).
- **Before:** scorer treats a 500-char snippet as the whole role; thin JD ->
  artificially low/odd scores.
- **After:** one line — "Job descriptions may be truncated; score on available
  evidence and do not penalize the candidate for a brief JD."
- **Measure:** before/after validation run — compare `scored_jobs` overall/track
  scores for Adzuna (truncated) vs ATS (full-text) postings; expect the gap to
  narrow. Version v2 -> v3.

## 6. Standardize "populate every field / `[]` not missing" guidance

- **Why:** present in `research_agent` / `resume_critic`, absent in
  `review_auditor`, `career_advisor`, `interview_coach`, `scoring_agent`. Explicit
  empty-list guidance is a known schema-repair reducer (ADR-078).
- **Before:** inconsistent; the agents without it omit empty list keys and trip the
  repair pass.
- **After:** the same short block in every structured agent prompt.
- **Measure:** schema-repair rate for the four agents that gain it (expect drop).

## 7. Document the two intentional `claim_type` vocabularies

- **Why:** tailoring uses `{reword, emphasize, remove, gap}`; the clinic
  (`resume_reviewer` + `resume_chat`) uses `{restate, reorder, quantify, reframe}`.
  Legitimate (different schemas) but a maintainer trap.
- **Before:** a reader assumes drift / a bug.
- **After:** a one-line comment in each noting the two are intentionally distinct
  (tailoring draft vs clinic overhaul), plus a one-line enum definition upfront in
  `tailoring_agent.txt` (today its taxonomy is scattered).
- **Measure:** none (documentation-only); reduces future mis-edit risk.

---

## Suggested rollout (grouped commits)

| Group | Items | Type | Primary metric |
|---|---|---|---|
| A. Correctness | 1, 2 | defect fix + version bumps | schema-repair rate -> 0 for fidelity_reviewer + review_auditor |
| B. Field-agnostic | 3 | principle application | non-tech validation run; forcing tests |
| C. Hardening | 4, 5, 6, 7 | small quality | schema-repair rate (6), score gap (5), version coverage (4) |

Each behavior change bumps the prompt's `# version:` so the before/after boundary
is marked in the logged `prompt_version` (log-only today; see the caveat above).
Defect fixes (group A) also warrant a `bugs/` RCA since they reached runtime via
the schema mismatch.
