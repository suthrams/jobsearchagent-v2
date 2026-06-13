# BUG-014: Prompt output instructions named fields that don't exist in the agent's schema

- **Severity:** Medium
- **Status:** Fixed
- **Reported:** 2026-06-12 (found during the prompt critical review)
- **Fixed:** 2026-06-12
- **Area:** `app/prompts/agents/fidelity_reviewer.txt`, `app/prompts/agents/review_auditor.txt`
- **Introduced by:** schema evolution that was not reflected back into the prompt text (the prompts kept an older output vocabulary).

## 1. What happened

Two agent prompts instructed the model to emit fields that do not exist in the
agent's Pydantic output schema:

- `fidelity_reviewer.txt` told the model to "Produce: `passed_suggestions` /
  `failed_suggestions` / `overall_verdict` (approved/rejected)". The
  `FidelityReview` schema has none of those — it has `overall_fidelity_status`
  (pass/fail/needs_revision), `approval_recommendation` (approve/revise/reject),
  `required_revisions`, `required_removals`, `unsupported_claims`, etc. The rest of
  the same prompt correctly targeted the real fields, so the prompt described two
  conflicting output shapes.
- `review_auditor.txt` told the model to "return `audit_passed: true`". The
  `ReviewAudit` schema has no `audit_passed` field; the pass/continue signal is
  `stop_recommendation` (bool) + `audit_score`. It also carried a contradictory
  mandate ("catch overlooked gaps the critic missed" vs "do not introduce new
  gaps").

A model that follows the stale instruction emits a key the schema rejects, forcing
a structured-output repair pass (extra latency + tokens, ADR-078) or a degraded
result on the system's final safety gate (fidelity) and its critique auditor.

## 2. Root cause

The output schemas evolved (FidelityReview gained the status/recommendation +
flag-list shape; ReviewAudit standardized on stop_recommendation) but the prompt
text was not updated in lockstep. Prompts and schemas are two halves of one
contract; only one half moved.

## 3. Why it was not caught

- Structured output is provider-enforced, so a stale field instruction degrades
  gracefully into a schema-repair retry rather than a hard crash — invisible
  unless someone reads the schema-repair telemetry per agent.
- No test cross-checked prompt output instructions against the schema field names,
  and prompt content is not exercised by the mocked-agent unit tests (they stub the
  provider, so the prompt text is never compared to the schema).
- The contradiction in review_auditor was textual and never asserted.

## 4. Prevention

- **The fix:** fidelity_reviewer (v5->v6) drops the stale block and its Output
  section now enumerates the real `FidelityReview` fields with empty-list guidance.
  review_auditor (v1->v2) removes `audit_passed`, enumerates the real `ReviewAudit`
  fields, and resolves the contradiction (a missed gap is reported in
  `missing_analysis_points` as an annotation, never by editing the critique).
- **Forcing function:** `tests/v2/test_bug014_prompt_schema_alignment.py` — asserts
  the two prompts do NOT contain the known-bad field names and DO reference their
  real schema's discriminator fields. Pins both prompts against re-drift.
- **Generalization:** the guard covers both prompts now; the broader lesson (prompt
  output fields must track the schema) is recorded in
  `docs/architecture/prompt_improvements.md`. A fuller cross-check (parse every
  prompt's Output field list and assert subset-of-schema) is a possible future
  forcing function noted there.
