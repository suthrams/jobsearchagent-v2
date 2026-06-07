# PII Data Flow and Handling - jobsearchagent-v2

> Companion to [`security.model.md`](security.model.md) Section 6 (PII Protection).
> This document traces every place Personally Identifiable Information (PII)
> enters the system, where it travels, what reaches the LLM provider, and where
> it rests. It then names the open gaps and the remediation plan.
>
> Backing ADRs: [ADR-020](adr/ADR-020-minimize-pii-sent-to-llms.md)
> (minimize PII sent to LLMs), [ADR-040](adr/ADR-040-define-data-retention-and-privacy-policy.md)
> (data retention/privacy), [ADR-015](adr/ADR-015-tailoring-must-be-evidence-bound.md)
> (Fidelity Reviewer is the one agent allowed to see `raw_text`),
> [ADR-062](adr/ADR-062-multi-user-profiles.md) (per-user isolation is cooperative,
> not enforced).

---

## 1. Scope and definitions

PII handled by this system, in descending sensitivity:

| Class | Examples | Where it originates |
|---|---|---|
| Direct identifiers | name, email, phone, physical address / location, profile links | resume upload |
| Quasi-identifiers | employer names, job titles, employment dates, education institutions, GPA | resume upload |
| Free-form PII carrier | `raw_text` (the full extracted resume - contains all of the above verbatim) | resume upload |

Job descriptions are **untrusted input**, not PII about the user, and are covered
by the prompt-injection section of `security.model.md` (Section 7), not here.

There are three PII surfaces, analyzed in turn:

- **Surface A** - PII sent to the LLM provider (Anthropic / OpenAI) at inference time.
- **Surface B** - PII at rest in `data/v2.db`.
- **Surface C** - PII in logs and observability.

---

## 2. Ingestion flow (where PII enters)

```text
POST /users/{user_id}/resume              app/api/routers/users.py:103-147
        |  (PDF UploadFile -> NamedTemporaryFile on local disk)
        v
ResumeParser.parse_pdf()                  app/services/resume_parser.py:77-99
        |  pdfminer.six extract_text() -> raw_text (full resume, all PII)
        v
ResumeParser.parse_text()                 app/services/resume_parser.py:101-181
        |  1. heuristic regex parse (name=first line, email regex, skills)
        |  2. Claude enhancement: SENDS FULL raw_text TO THE LLM  <-- Surface A
        v
ResumeProfile (Pydantic)                  app/schemas/resume_profile.py:41-68
        |  raw_text is a MANDATORY field on the profile (line 46)
        v
ResumeRepository.create()                 app/repositories/resume_repository.py:19-35
        |  INSERT INTO resumes (raw_text, parsed_profile_json, ...)  <-- Surface B
        v
data/v2.db  (plaintext SQLite)
```

Key fact carried through the rest of this document: **`parsed_profile_json`
contains `raw_text`**, because `raw_text` is a required field on `ResumeProfile`
and the parser persists `profile.model_dump()`. Anything that loads the parsed
profile and forwards it without stripping `raw_text` is forwarding the entire
resume.

---

## 3. Surface A - PII sent to the LLM provider

> **Status (2026-05-30): findings A1 and A2 below are RESOLVED by
> [ADR-069](adr/ADR-069-redact-direct-identifiers-at-the-llm-seam.md).** The
> redaction seam described in 3.1 now strips direct identifiers in addition to
> `raw_text`, all three clinic context sites route through it, and a boundary
> invariant test (`tests/v2/test_pii_redaction_invariant.py`) prevents
> regression. The original analysis is preserved below for the record; the
> "current behavior" prose has been updated to the post-fix state.

### 3.1 The redaction seam (post ADR-069)

`app/services/context_trimmer.py::redact_pii_for_llm` (wrapped by the
established `trim_resume_profile`, and by `project_resume_for_scoring` for the
scoring context - ADR-086) is the single chokepoint every agent context
routes through. It now:

```python
out = {k: v for k, v in profile.items() if k != "raw_text"}   # drop raw_text
out["name"] = "[CANDIDATE]"                                    # placeholder when present
out["email"] = out["location"] = out["file_name"] = None       # drop direct identifiers
```

It removes `raw_text` **and** the direct identifiers (name -> placeholder; email,
location, file_name -> None). Kept: headline, summary, skills, skill_groups,
experience[], education[], certifications[] - the quasi-identifiers agents reason
over. Before ADR-069 this dropped only `raw_text`, leaking name/email/location to
every agent (finding A1).

`BaseAgent._run` still forwards the context dict straight to
`provider.complete()` and `PromptLoader` serializes it as-is - so redaction must
happen before the profile enters the context, which is exactly what the seam
guarantees. The free-text `headline` / `summary` fields are kept (agents reason
over them) but have inline **phone numbers and email addresses scrubbed** to
`[PHONE]` / `[EMAIL]` via a deterministic regex (ADR-069 addendum) - this closes
the common case of a phone number on the resume's headline/contact line. Residual:
a **name** written into that prose is still not redacted (needs NER; accepted, see
ADR-069 "out of scope").

### 3.2 Per-agent PII reaching the model (post ADR-069)

"Contact PII" = name (now `[CANDIDATE]` placeholder) / email / location, all
redacted at the seam. "raw_text?" = does raw resume text reach this agent.

| Agent                    | Sends profile?                      | raw_text reaches it?                | Contact PII sent | Quasi-id PII (companies/titles/dates/schools) |
| ------------------------ | ----------------------------------- | ----------------------------------- | ---------------- | --------------------------------------------- |
| Research Agent           | no                                  | no                                  | none             | none                                          |
| Scoring Agent            | yes (`score_jobs.py:110`)           | no (redacted)                       | no (redacted)    | yes                                           |
| Resume Critic            | yes (`deep_review_runner.py:66`)    | no (redacted)                       | no (redacted)    | yes                                           |
| Review Auditor           | yes (`deep_review_runner.py:98`)    | no (redacted)                       | no (redacted)    | yes                                           |
| Career Advisor           | yes (`career_advice.py:63`)         | no (redacted)                       | no (redacted)    | yes                                           |
| Interview Coach          | yes (`interview_prep.py:68`)        | no (redacted)                       | no (redacted)    | yes                                           |
| Tailoring Agent          | yes (`tailoring.py:227`)            | no (redacted)                       | no (redacted)    | yes                                           |
| Fidelity Reviewer (tailoring) | yes (`tailoring.py:260`)       | no (redacted)                       | no (redacted)    | yes                                           |
| Fidelity Reviewer (clinic) | yes (`resume_clinic_runner.py:240`) | **yes, top-level (ADR-015)**      | no (redacted)    | yes                                           |
| Resume Reviewer (Clinic) | yes (`resume_clinic_runner.py:116`) | no (redacted, ADR-069)              | no (redacted)    | yes                                           |
| Resume Chat (clinic)     | yes (`resume_clinic.py:467`)        | no (redacted, ADR-069)              | no (redacted)    | yes                                           |

### 3.3 Findings on Surface A

- **A1 - Structured contact PII is sent to every reasoning agent, though none of
  them need it.** Scoring, critique, audit, advice, interview prep, and tailoring
  all reason about *job fit* and *resume content*; none requires the candidate's
  name, email address, or physical location to do that. ADR-020's implementation
  note - "Avoid sending phone, email, address unless required" - is therefore only
  half-honored: we strip `raw_text` but still ship the direct identifiers in the
  structured fields. **This is the primary hole.**

- **A2 (RESOLVED by ADR-069) - The Resume Clinic leaked `raw_text` to the Resume
  Reviewer.** The clinic built three contexts from `parsed_profile` (which
  includes the mandatory `raw_text`) **without** redaction: the reviewer context
  (`resume_clinic_runner.py:116`), the chat-revise context
  (`resume_clinic.py:467`), and the fidelity cached block
  (`resume_clinic_runner.py:238`). Inline comments claimed `raw_text` was absent;
  the code did not implement it. All three now route through `redact_pii_for_llm`.
  This was a correctness bug *and* a privacy gap.

- **A3 - Accepted exception:** the **clinic Fidelity Reviewer** receives
  `raw_text` (ADR-015) because it must verify that rewrites cite real resume
  content. It is passed **top-level**, outside the redacted profile block
  (`build_fidelity_context_for_overhaul`, `resume_clinic_runner.py:259`). Together
  with the resume parser's enhance call (which must read `raw_text` to parse it),
  these are the only sanctioned `raw_text`-to-LLM paths. Note the *tailoring*
  Fidelity Reviewer does **not** receive `raw_text` today - it validates against
  the structured profile only.

- **A4 - Vendor data handling is out of our control once sent.** Whatever crosses
  Surface A is governed by the provider's retention/training policy, not ours.
  Minimizing what we send is the only lever we own. (This is the "vendor PII"
  concern tracked for the security article.)

---

## 4. Surface B - PII at rest

`data/v2.db` is vanilla SQLite (header "SQLite format 3"). There is **no
encryption at rest** - no SQLCipher, no column encryption, no OS-level guarantee
beyond filesystem permissions.

| Table.column | PII held | Notes | Ref |
|---|---|---|---|
| `resumes.raw_text` | full resume verbatim | plaintext; persisted indefinitely | `database.py:63-65` |
| `resumes.parsed_profile_json` | name, email, location, headline, summary, experience[], education[] (includes `raw_text`) | plaintext | `database.py:65` |
| `workflow_runs.state_json` | full serialized `WorkflowState` incl. the resume profile + all agent outputs | plaintext; one row aggregates a whole run's PII | `database.py:31-33` |
| `tailored_resumes.tailored_json` / `edited_json` | resume excerpts and rewrites | plaintext | `database.py:119-131` |
| `resume_clinic_reviews.review_json` / `overhaul_json` | scorecard + evidence-bound rewrites | plaintext | `database.py:143-159` |
| `review_rounds.*_json`, `resume_reviews.review_json`, `career_advice.advice_json`, `interview_prep.prep_json`, `job_scores.score_json` | gap/strength text that may quote resume content | plaintext | `database.py:76-117` |
| `memory_items.memory_value_json` | typed preferences only - **no raw resume text, no names** by invariant | plaintext but low sensitivity | `database.py:240-250` |

### Findings on Surface B

- **B1 - No encryption at rest.** Any process or person with read access to
  `data/v2.db` reads every resume in cleartext. ADR-040's "avoid indefinite raw
  resume storage" is not yet implemented.
- **B2 - No automatic retention/purge.** `raw_text` lives forever unless a row is
  manually deleted. `DELETE /users/{user_id}/resume/{resume_id}` exists and
  cascades to clinic reviews, but there is no time-based purge. **Design ratified
  in [ADR-070](adr/ADR-070-data-retention-and-state-deduplication.md) (Phase 1 of
  the at-rest track); implementation pending** - completes/wires `purge_old_data()`
  to the PII tables with cascade and an explicit trigger.
- **B3 - `raw_text` is duplicated** into both `resumes.raw_text` and
  `resumes.parsed_profile_json` (and again into `workflow_runs.state_json`),
  widening the at-rest blast radius. **ADR-070 de-duplicates the `state_json`
  copy** (stores the redacted profile in state instead of the full `model_dump()`),
  removing `raw_text` + direct identifiers from `state_json` and the LangGraph
  checkpoints blob; implementation pending.

---

## 5. Surface C - logs and observability

This surface is in good shape and is recorded here so remediation does not
regress it:

- **C1 (good)** - `llm_calls` stores only token counts, cost, and latency. It
  does **not** store prompt or response text. `database.py:203-216`.
- **C2 (good)** - `agent_events.input_summary` / `output_summary` are truncated
  summaries (cap ~500 chars), not raw payloads. `base_agent.py`.
- **C3 (good)** - Python logging uses IDs and counters, not content
  (e.g. `upload_resume failed for user_id=%s`). No raw resume text, email, or
  phone is logged.
- **C4 (good)** - Memory is typed and excludes raw PII by the memory invariant
  (`security.model.md` Section 11).

---

## 6. Conformance scorecard vs the ADRs

| Promise | Source | Status |
|---|---|---|
| Prefer parsed profile over raw text for agents | ADR-020 | Met |
| Avoid sending email / phone / address unless required | ADR-020 | **Met** (ADR-069 closed A1) |
| Do not log raw resume text | ADR-020 | Met (C1-C3) |
| Only Fidelity Reviewer sees `raw_text` | ADR-015 | **Met** (ADR-069 closed A2) |
| Avoid indefinite raw resume storage | ADR-040 | **Partial** (B2/B3 design ratified in ADR-070, impl pending; B1 encryption deferred to Phase 2) |
| Support delete operations | ADR-040 | Met (per-resume delete exists) |

The remaining open gaps are B1 (at-rest encryption, Phase 2) and the *pending
implementation* of ADR-070's retention + de-duplication (B2/B3, Phase 1). Both
are tracked in the ADR-040 / at-rest track in Section 7.

---

## 7. Remediation plan

Ratified in [ADR-069](adr/ADR-069-redact-direct-identifiers-at-the-llm-seam.md)
(redact direct identifiers at the LLM context seam). Ordered by privacy impact
per unit of effort.

1. **[DONE - ADR-069] Strip direct identifiers before every LLM call (closed
   A1).** `redact_pii_for_llm(profile)` (in `context_trimmer.py`, wrapped by
   `trim_resume_profile`) removes `raw_text` and the direct identifiers
   (`name` -> `[CANDIDATE]`; `email` / `location` / `file_name` -> None), leaving
   the experience, skills, and education content agents reason over. Applied at
   the single seam so no agent can opt out. Reasoning quality is unaffected (no
   agent conditions on identity; the renderer re-inserts it from the stored
   profile).
2. **[DONE - ADR-069] Route the clinic profiles through the seam (closed A2).**
   The reviewer, chat-revise, and fidelity cached-block contexts now call
   `redact_pii_for_llm`. The clinic Fidelity Reviewer keeps its sanctioned
   top-level `raw_text` path (A3).
3. **[DONE - ADR-069] Invariant test at the seam.**
   `tests/v2/test_pii_redaction_invariant.py` exercises the pure clinic fidelity
   builder and source-scans every `resume_profile` context site in `app/`,
   failing the build if one bypasses the redaction helper (state-write sites are
   explicitly allowlisted). Forcing function, consistent with the model-pin
   invariant.
4. **[RATIFIED, IMPL PENDING - ADR-070] Retention + de-duplication (Phase 1,
   closes B2 and B3).**
   [ADR-070](adr/ADR-070-data-retention-and-state-deduplication.md) ratifies the
   spike's Option A: complete and wire `purge_old_data()` to the PII tables with
   cascade (a purged run deletes its child rows; resumes purged on a separate
   longer window guarded by the active flag and a run-reference check), behind an
   explicit trigger (`POST /admin/purge` + CLI, no scheduler); and de-duplicate the
   profile out of `state_json` by storing the redacted profile in state. Design
   ratified; implementation pending.
5. **[OPEN - Phase 2] Encryption at rest (closes B1).** Deferred to a separate
   Phase 2 ADR. Per the
   [spike](spike_data_at_rest_security.md): app-level field encryption (Option B),
   with SQLCipher (Option C) reserved for a hosted/multi-user future. SQLCipher is
   penalized because the LangGraph `SqliteSaver` shares `data/v2.db` with no key
   support and needs a Windows-compiled dependency. Sequenced after retention
   because bounding the pile (Phase 1) shrinks the surface encryption must protect,
   and the send side (ADR-069) was the larger live exposure.

Findings A1 and A2 are the immediate "hole" the send-side work plugged; B2/B3
(ADR-070, Phase 1) and B1 (Phase 2) are the follow-on at-rest hardening.

---

## 8. Maintenance

Update this document whenever a new agent is added, the trimmer changes, a new
PII-bearing column is added to `data/v2.db`, or the redaction/retention posture
changes. The conformance scorecard (Section 6) is the quick check: every "Not
met" row is an open privacy gap.
