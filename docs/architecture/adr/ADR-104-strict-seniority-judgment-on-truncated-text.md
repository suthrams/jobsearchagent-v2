# ADR-104: Strict Seniority Judgment in the Relevance Filter (World-Knowledge When Text Is Truncated)

## Status

- **Proposed** (2026-06-12). Surfaced by live validation run `db64041b` (profile 1): senior cleared roles scored for a fresh-grad profile.
- Refines ADR-079 (relevance pre-filter); same family as ADR-094 (clearance-in-filter).

## Context

- Adzuna hard-truncates descriptions to **500 chars**; the "10+ years / TS-SCI" requirement lives *past* the cut.
- All three layers fail on that blind spot: deterministic `exceeds_cap` reads the snippet; the title-seniority filter only catches explicit markers; the relevance prompt (v3) is told *"reason ONLY from posting text… keep when unstated"*.
- Result: Fort Meade DNEA / "Target Digital Network Analyst" roles (senior, cleared, neutral titles) passed every filter for a `max_years=3, exclude_senior` profile and consumed scoring budget.
- Full-text sources (Greenhouse JDs 6-7k chars) don't have this problem - it's a **truncated-text** class, not a role class.

## Constraints

- **Field-agnostic.** No hardcoded role lexicon (no "DNEA is senior"); derive "typical seniority" from the role title/family generally. Pinned by the existing field-agnostic forcing test.
- **Data-driven.** Strictness keys off the profile's own `seniority_signals` (`exclude_senior`, low `max_years_experience`), not a profile identity.
- **Scoped.** Strictness applies to the **seniority axis only**; the role-suitability/adjacency axis stays recall-oriented (ADR-079's generous-adjacency behavior is preserved).

## Decision

- Bump `relevance_filter.txt` v3 -> **v4** (observable in `llm_calls`). Two scoped changes:
  - **Seniority axis may use world-knowledge when text is silent:** descriptions are often truncated, so for the SENIORITY axis the model MAY use general knowledge of what a role title/family TYPICALLY requires; if that typical level is materially above the candidate's `max_years` window (or clearly senior when `exclude_senior`), mark `too_senior` even when the posting text states no years. Derive "typical" generally - do NOT assume any industry.
  - **Precision on the seniority axis for early-career profiles:** the "keep when ambiguous" default applies to ROLE SUITABILITY; for SENIORITY of an early-career candidate, prefer precision - when the role cannot reasonably be placed at/below the candidate's band, treat it as `too_senior`.
- No code change: the node already passes `seniority_signals` (min/max years, exclude_senior) and the redacted profile. Prompt-only.

## Decision review (not a rubber-stamp)

- **Recommendation:** scoped v4 prompt change (world-knowledge + precision on seniority for early-career). **Confidence: medium** - it relaxes the long-standing "reason only from text" rule and leans on model world-knowledge, which is inherently fuzzier than deterministic text.
- **Load-bearing decision:** *which axis flips to precision.* Only seniority for early-career profiles; suitability stays recall-first. This keeps ADR-079's adjacency win while fixing the over-experienced leak.
- **Alternatives:**
  - Fetch the full JD for truncated sources - fixes the root, but network cost + dead-link risk + Adzuna redirect URLs; de-bundled (heavier, separate).
  - Deterministic role-seniority lexicon - brittle and profile/industry-specific; **rejected** (violates field-agnostic principle).
  - Leave as-is - rejected; the user observed real over-experienced jobs scored.
- **Pros:** overcomes truncated snippets using knowledge the model already has; field-agnostic; data-driven; no new cost (same one batched Haiku call).
- **Cons / risks (estimated, not measured):** world-knowledge inference can over-drop entry roles with neutral titles or mis-judge an unusual title; drop-on-ambiguity trades recall for precision (the user explicitly asked for stricter experience). Only affects profiles that opt into `search.relevance_filter` AND set early-career signals.
- **Reversibility:** high - revert the prompt to v3. No schema/code change.
- **Reasons to say NO:** if false-drops of legit entry roles prove worse than the over-experienced noise; mitigated by scoping to early-career + seniority axis and keeping suitability recall-first.

## How it integrates

- Unchanged wiring: opt-in `search.relevance_filter`, one batched Haiku call between `load_resume` and `score_jobs`, never-lose-the-run on failure, PII redaction via `trim_resume_profile`.
- Profiles without `relevance_filter` are unaffected (deterministic filters only - their known text-based limit stands; the reasoning gate is where nuanced experience judgment belongs).

## Out of scope (de-bundled)

- Full-JD fetch for truncated sources (root-cause fix, heavier).
- Applying world-knowledge seniority inference to the deterministic `seniority_filter` (kept text-based + explicit-marker only).

## PSSR

- **Performance:** no change (same single batched call).
- **Scalability:** none.
- **Security:** none new - same redacted profile + untrusted-JD-as-data guardrails.
- **Reliability:** unchanged never-lose-the-run; a fuzzier verdict is still a keep/drop the node handles.

## Tests

- Update the version pin: `relevance_filter.txt` starts with `# version: 4`.
- Keep the field-agnostic forcing test green (no hardcoded role/industry; "derive"/"do not assume"/"different profession" present).
- Add: prompt instructs seniority world-knowledge when text is silent + precision on the seniority axis for early-career (assert the v4 language, field-agnostic).
- File: `tests/v2/test_adr079_relevance_filter.py` (+ version pin update there).

## References

- Validation run `db64041b` (over-experienced-jobs evidence); ADR-079 (the filter); ADR-094 (clearance-in-filter); BUG-010 (the truncated-snippet class).
