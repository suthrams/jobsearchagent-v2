# BUG-002: Job-focused Resume Clinic chat edits frozen in preview and clobbered on save

- **Severity:** Critical (silent data loss - destroys the user's work)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08
- **Area:** `app/ui/components/resume_chat_panel.py`, `app/repositories/resume_clinic_repository.py` (`set_decision`)
- **Introduced by:** ADR-072 (14c4839, reuse of the clinic chat panel on a job-seeded session) compounding a pre-existing `set_decision` behavior

## 1. What happened

In the job-focused Resume Clinic ("Focus a job" -> live chat, ADR-090/072), a user ran
five chat turns to refine a resume. The live preview never changed across the turns
(the feedback looked ignored), and after clicking **Save final edit** the exported
resume contained NONE of the chat changes. In the DB, session `c352756b`'s `edited_json`
was byte-identical to the original agent overhaul (11 rewrites) even though the chat
agent had grown it to 12 rewrites (summary reworded, experience reframed, skills
grouped) - all five turns were lost.

## 2. Root cause

Two defects in series:

1. **Frozen preview / stale UI state.** The shared chat panel refreshed its held state
   (`st.session_state[state_key]`) after each turn by calling
   `api.list_resume_clinic_runs(user_id)` and finding the matching row. That endpoint is
   `ResumeClinicRepository.list_by_user`, which filters `WHERE job_id IS NULL` (ADR-072
   keeps job-anchored sessions out of the job-agnostic past-runs list). For a job-focused
   session (`job_id` set) the lookup returned nothing, so the held state was never
   updated. It stayed the initial seed (`edited=None`), so `compose_resume` kept
   rendering the original overhaul, and `_rc_edited` stayed `None`.

2. **Decision clobbers edits.** **Save final edit** sends
   `_edited_payload = _rc_edited or _rc_overhaul`. With `_rc_edited` stuck at `None`, it
   sent the *original* overhaul. `ResumeClinicRepository.set_decision` then
   *unconditionally* wrote `edited_json = json.dumps(edited)` (NULL when omitted), so it
   overwrote the server's accumulated chat edits with the original overhaul.

The server side was correct the whole time (each chat turn persisted `edited_json` via
`set_edited`); the UI read it back through the wrong door and the decision wrote over it.

## 3. Why it was not caught

- The chat panel was extracted "verbatim" for ADR-072 reuse. Its refresh-via-`list`
  logic was correct for the **job-agnostic** clinic (where `list_by_user` *does* return
  the session) and was only ever tested there. No test exercised the panel against a
  `job_id`-anchored session, which is exactly the row class `list_by_user` is designed to
  exclude. The exclusion (a deliberate ADR-072 feature) and the panel's reuse (ADR-072)
  shipped in the same change without a test crossing them.
- Streamlit UI logic is not unit-tested (only headlessly smoke-rendered), so the stale
  `st.session_state` path had no automated coverage at all.
- The `set_decision` clobber was invisible because every repository test created the row
  with no prior `edited_json`; nulling an already-null column looks like a no-op. No test
  set `edited_json` first (a chat turn) and *then* submitted a payload-less decision.

## 4. Prevention

- **The fix:** the panel now refreshes `state_key` from the chat **response** (which
  carries the new `overhaul` + `fidelity_review`), not the list endpoint - works for both
  clinic types and removes the dependency on a job-excluding query. Discard mirrors the
  server clear locally. `set_decision` now only writes `edited_json` when an explicit
  payload is supplied; a payload-less decision leaves it untouched (also matches
  `compose_resume`, which applies `edited` on `approve`). ADR-091.
- **Forcing function:** `tests/v2/test_resume_clinic_repository.py::test_set_decision_without_payload_preserves_chat_edits`
  fails the build if a decision ever clobbers accumulated edits again. Router coverage:
  `tests/v2/test_resume_clinic_router.py::test_chat_feeds_prior_fidelity_into_agent_context`
  exercises multi-turn state on a clinic session.
- **Generalization:** the repo guard catches the whole "decision wipes edits" class. The
  UI-refresh gap is only partially guarded (no Streamlit unit harness); the structural
  fix (refresh from the response, never from a filtered list) removes the failure mode at
  the source rather than relying on a test.
