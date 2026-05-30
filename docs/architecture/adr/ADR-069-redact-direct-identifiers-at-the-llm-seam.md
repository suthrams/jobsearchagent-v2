# ADR-069: Redact Direct Identifiers at the LLM Context Seam

## Status

Accepted (2026-05-30). Implemented.

Operationalizes ADR-020 (Minimize PII Sent to LLMs), which was only half-honored
in practice. Preserves the ADR-015 exception (the Fidelity Reviewer is the one
agent allowed to see `raw_text`). See `pii_data_flow.md` for the full data-flow
trace and gap analysis this ADR closes.

## Context

`pii_data_flow.md` traced every place PII reaches an LLM and found two send-side
gaps:

- **A1 (primary).** `app/services/context_trimmer.py::trim_resume_profile` drops
  only `raw_text`. Every reasoning agent (Scoring, Resume Critic, Review Auditor,
  Career Advisor, Interview Coach, Tailoring, Fidelity, Resume Reviewer) still
  receives the candidate's `name`, `email`, and `location` in the structured
  profile. None of these agents reason about job fit, gaps, advice, or rewrites
  using the candidate's name or contact details. ADR-020's note - "avoid sending
  email/phone/address unless required" - is therefore not implemented for the
  structured fields.
- **A2.** The Resume Clinic (`app/services/resume_clinic_runner.py:116`) forwards
  the parsed profile to the Resume Reviewer **without** calling `trim_resume_profile`,
  so the mandatory `raw_text` field (present in `parsed_profile_json`) reaches the
  reviewer. This violates the ADR-015 rule that only the Fidelity Reviewer sees
  `raw_text`. The inline comment there asserts the opposite of what the code does.

A key enabling fact makes redaction safe: the deterministic resume renderer
(`app/services/resume_text_renderer.py::compose_resume`, lines 210-213) reads
`name` / `email` / `location` from the **stored** profile dict, not from any LLM
output. The header block of a rendered/tailored resume is reconstructed from the
un-redacted profile at render time. Agents never need identity to produce their
structured outputs, and removing it from their context cannot degrade the final
artifact.

There is no field-level redaction layer today: `BaseAgent._run` forwards the
context dict straight to the provider, and `PromptLoader` serializes it as-is.

## Decision

Introduce a single redaction step applied to the resume profile wherever it is
placed into an agent context, plus an invariant test that makes the rule
self-enforcing.

### A. Redaction set (structured fields)

Redact the **direct identifiers** from the profile before it enters any agent
context:

- `name` -> replaced with a stable placeholder (`"[CANDIDATE]"`), not dropped, so
  prompt text that references the field still reads naturally.
- `email` -> dropped (set to `None`).
- `location` -> dropped (set to `None`).
- `file_name` -> dropped (set to `None`); it commonly encodes the candidate name
  (e.g. `John_Doe_CV.pdf`).
- `raw_text` -> dropped (already done by `trim_resume_profile`).

**Kept** (agents reason over these; they are quasi-identifiers, not direct
identifiers): `headline`, `summary`, `skills`, `skill_groups`, `experience[]`
(company, title, dates, description), `education[]` (institution, degree, year,
GPA, honors), `certifications[]`.

Rationale for the split: name/email/location are never load-bearing for fit
scoring, gap analysis, advice, interview prep, or evidence-bound rewriting, and
they are the highest-value direct identifiers. Employer/title/education are
load-bearing (you cannot assess fit without them) and are lower-sensitivity
quasi-identifiers. Phone and street address are not structured profile fields -
they exist only inside `raw_text`, which is already withheld from every agent
except Fidelity.

### B. One seam, folded into the existing helper

Extend `trim_resume_profile` so it performs the redaction in A in addition to
dropping `raw_text` (or add `redact_pii_for_llm` and have `trim_resume_profile`
call it - same chokepoint either way). Every current caller
(`score_jobs.py`, `deep_review_runner.py`, `career_advice.py`,
`interview_prep.py`, `tailoring.py`) then gets redaction for free.

Fix A2 by routing all three Resume Clinic profile contexts through the seam -
the reviewer (`resume_clinic_runner.py:116`), the chat-revise context
(`resume_clinic.py:467`, ADR-068), and the clinic fidelity cached block
(`resume_clinic_runner.py:238`) - making their inline comments true.

### C. The raw_text-to-LLM exceptions are explicit and narrow

Two sanctioned paths carry raw resume text to an LLM, both by necessity:

- **The resume parser's enhance call** must read `raw_text` to parse it into the
  structured profile in the first place. This is inherent to parsing.
- **The clinic Fidelity Reviewer** receives `raw_text` (ADR-015) to verify that
  rewrites cite real resume content. It is passed **top-level**, outside the
  redacted profile block (`build_fidelity_context_for_overhaul`), so the profile
  block it sees is redacted like everyone else's.

Everywhere else - including the *tailoring* Fidelity Reviewer, which validates
against the structured profile only - receives the redacted profile with no
`raw_text`.

### D. Invariant test at the seam (the enforcement mechanism)

Because the redaction lives in a helper that callers must remember to use,
`tests/v2/test_pii_redaction_invariant.py` asserts the property directly in two
layers:

- It exercises the pure clinic fidelity-context builder and asserts the cached
  profile block is redacted while `raw_text` is preserved top-level (the A3
  exception).
- It source-scans every `"resume_profile":` site in `app/` and fails if one does
  not route through `redact_pii_for_llm` / `trim_resume_profile`. The two sites
  that write the profile into `WorkflowState` (not an LLM context) -
  `load_resume.py` and the initial state in `workflows.py` - are an explicit,
  self-guarding allowlist (a second test fails if an allowlisted site is moved or
  its right-hand side changes, forcing re-justification).

A new agent or context site that forgets the helper fails the build. This mirrors
the model-pin invariant (`tests/v2/test_model_pins.py`): a forcing function, not
a behavioral test.

### Out of scope

- **Free-text scrubbing.** `headline` and `summary` are prose that could, in
  principle, contain the candidate's name or a contact detail. v1 of this seam
  does not scrub free text. Residual risk is accepted and recorded; a softer
  control (instructing the parser prompt to keep `summary` free of direct
  identifiers) can follow.
- **PII at rest.** Encryption of `raw_text` / `state_json` and a time-based purge
  (ADR-040, findings B1/B2 in `pii_data_flow.md`) are a separate, higher-effort
  track. This ADR is send-side only - the larger live exposure.
- **Coarse location for fit.** If location-aware scoring is ever needed, a coarse
  region (not the stored `location` string) can be reintroduced behind a knob;
  out of scope here.

## Options considered

- **Redact in `trim_resume_profile` + boundary invariant test (chosen).** Minimal
  change, reuses the existing chokepoint, and the test prevents silent
  regression. Limitation: callers must use the helper - mitigated by the test.
- **Scrub at the `PromptLoader` / `BaseAgent` boundary (final guard).** Rejected
  as the primary mechanism: the loader sees an arbitrary nested context dict, not
  a typed profile, so it cannot reliably tell a profile field from job-description
  text, and it would need an allowlist carve-out for Fidelity's `raw_text`. Viable
  later as defense-in-depth on top of the chosen seam, not instead of it.
- **Drop the `name` field entirely instead of placeholdering.** Rejected: a
  placeholder keeps prompt phrasing natural and avoids any agent emitting a
  literal empty/null where a subject is expected; identity is restored by the
  renderer regardless.
- **Redact at parse time (store a redacted profile).** Rejected: the stored
  profile is the source of truth for the deterministic renderer's header and for
  the user's own view; redaction belongs at the egress to the LLM, not at rest.

## Consequences

### Positive

- ADR-020 becomes truly implemented for the structured fields: direct identifiers
  no longer leave the system to the model provider.
- Closes A2 - only the Fidelity Reviewer sees `raw_text`, as ADR-015 intends.
- No quality regression: agents never used identity to reason, and the renderer
  reconstructs the header from the stored profile.
- The invariant test makes the property durable across new agents.

### Tradeoffs

- Free-text `summary` / `headline` may still carry a name; not scrubbed in v1
  (documented residual risk).
- One more transformation between the stored profile and the agent context;
  contributors must route new profile-bearing contexts through the helper (the
  test enforces this).

### Neutral

- Docs: ADR-069 + index, `pii_data_flow.md` (flip findings A1/A2 and the
  conformance scorecard rows once implemented), `security.model.md` Section 6,
  CLAUDE.md prompt rules ("never send raw resume text" extended to "send the
  redacted profile; only the Fidelity Reviewer sees raw_text").
- Tests: redaction unit cases (name placeholdered, email/location/raw_text gone,
  experience/education/skills intact), clinic-routes-through-helper, and the
  boundary invariant across all context sites.

## References

- ADR-020 - Minimize PII Sent to LLMs (the policy this operationalizes).
- ADR-015 - Tailoring Must Be Evidence-Bound (the Fidelity Reviewer `raw_text`
  exception).
- ADR-040 - Data Retention and Privacy Policy (the at-rest track this defers to).
- ADR-058 - Per-workflow model snapshot + model-pin invariant (the
  forcing-function test pattern reused here).
- `pii_data_flow.md` - the end-to-end trace and gap analysis.
