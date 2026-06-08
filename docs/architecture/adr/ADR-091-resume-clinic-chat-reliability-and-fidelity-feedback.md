# ADR-091: Resume Clinic Chat Reliability, Fidelity Feedback, and Export Fidelity

## Status

Accepted (2026-06-08). Bug-fix + refinement ADR (no new agent, endpoint, or table).

Builds on ADR-066 (Resume Clinic), ADR-068 (clinic chat-revise + export, session cost
meter), ADR-072 (tailoring live chat reuses the clinic chat stack on a job-seeded
draft), ADR-090 (job-focused clinic / "Focus a job"), ADR-059 (evidence-bound
tailoring; the human is the final author and is not re-policed).

## Context

End-to-end testing of the job-focused Resume Clinic (ADR-090 "Focus a job" -> the
shared chat panel from ADR-072/068) surfaced four defects in one session
(`c352756b`). Root causes, all evidence-backed from `data/v2.db`:

1. **Chat edits were frozen in the preview and then clobbered on save.** The shared
   chat panel (`app/ui/components/resume_chat_panel.py`) refreshed its held state by
   calling `list_resume_clinic_runs`, but that endpoint is
   `ResumeClinicRepository.list_by_user`, which filters `WHERE job_id IS NULL` (ADR-072
   keeps job-anchored sessions out of the job-agnostic past-runs list). For a
   **job-focused** session (`job_id` set) the refresh matched nothing, so
   `st.session_state[state_key]` never updated:
   - the live preview kept rendering the *original* overhaul every turn
     (`compose_resume` saw `edited=None`), so the user's feedback looked ignored even
     though the server was applying it; and
   - **Save final edit** sent `_rc_edited or _rc_overhaul` = the original overhaul,
     overwriting the server's accumulated chat edits. The persisted `edited_json` ended
     byte-identical to the untouched agent draft.

   A second, latent contributor: `ResumeClinicRepository.set_decision` *unconditionally*
   overwrote `edited_json` with whatever the caller passed (NULL when omitted) on every
   decision — so even with the UI fixed, an `approve` after chatting (where
   `compose_resume` is supposed to apply the chat edits) would wipe them.

2. **Messy export (duplicated bullets).** `resume_text_renderer._apply_rewrites`
   matched each rewrite to a source bullet by exact text or `original ⊂ bullet`
   substring, and **appended** on no match ("never silently drop"). When a rewrite's
   `original_text` *merged two source bullets* into one string (longer than either
   bullet, so the substring test fails), the rewrite was appended while both originals
   stayed — a 3-bullet role rendered as 5 bullets, in every format (md/txt/html/docx/pdf
   share the composed intermediate).

3. **The fidelity loop never converged.** Every chat turn's Fidelity Reviewer verdict
   was `fail`/`reject` with unsupported claims, but the verdict was never fed into the
   next chat turn's context, so the agent re-asserted the same claims and the reviewer
   re-flagged them. The user relayed the feedback manually and it still never landed.

4. **Cost felt high for one job.** Five chat turns cost ~$0.33 (Sonnet chat ~$0.05 +
   Haiku fidelity ~$0.01 per turn). The dominant driver was the *number of turns*
   (a direct consequence of #3 never converging), not the per-turn price.

5. **The exported PDF was garbled.** Rasterising the real export surfaced three
   `render_pdf` defects (the other formats were fine): (a) the contact line and flat
   skills list rendered the literal text `&middot;` because the `P()` helper escapes
   `&`, and the call sites pre-built a `&middot;` HTML entity, so `&` -> `&amp;`;
   (b) every list item rendered the literal word `bullet` as its marker because
   `ListItem(value="bullet")` overrides the `•` glyph with that text; and (c) the few
   characters outside ReportLab's WinAnsi (CP1252) Type-1 font encoding - e.g. the
   non-breaking hyphen U+2011 - rendered as notdef black boxes.

## Decision

### A. Job-focused chat refreshes from the chat response, not the list endpoint

The shared chat panel now updates its held state directly from the **chat turn's
response** (which carries the new `overhaul` and `fidelity_review`) instead of
re-fetching via `list_resume_clinic_runs`. This works for both clinic types and
removes the dependency on a list endpoint that excludes job-anchored sessions. The
"Discard chat edits" path mirrors the server-side clear locally for the same reason.

### B. `set_decision` never wipes accumulated chat edits

`ResumeClinicRepository.set_decision` only writes `edited_json` when an explicit
`edited` payload is supplied; a decision with `edited=None` leaves `edited_json`
untouched. This matches `compose_resume`'s contract (prefer `edited` whenever
populated, *except* `reject`, which ignores it) and is a defensive backstop for (A).
`reject` needs no clear because the composer already ignores `edited` for it.

### C. Renderer collapses merged-bullet rewrites instead of duplicating

`_replace_or_append_bullet` gains two layered cases before the append fallback:
- **merge collapse:** when one or more source bullets are substrings of a rewrite's
  `original_text` (the rewrite merged them), replace the first and delete the rest
  (min-length guard so a trivial fragment isn't swallowed); and
- **token-overlap fallback:** replace the single best-matching bullet when Jaccard
  word overlap clears a floor (0.6) and beats the runner-up by a margin (0.15) — handles
  light rewording of `original_text` across chat turns. An ambiguous tie still appends.

The "never silently drop" guarantee is preserved for genuinely new content.

### D. The prior fidelity verdict is fed back into the chat agent

The chat endpoint passes a compact prior verdict (`{status, recommendation,
unsupported_claims}`, capped) into the agent context as `prior_fidelity`. The
`resume_chat` prompt (v3) gains a load-bearing "Fidelity self-correction" section: when
`prior_fidelity` is not `pass`, the agent must ground or remove the flagged claims this
turn (in addition to the user's request), must not re-assert a rejected claim verbatim,
and must say so in `reply`. Grounding means making a claim *smaller/truer*, never
inventing support. The verdict is surfaced in the UI panel so the user sees what is
flagged. This is the convergence mechanism — and therefore the real cost lever.

### E. Fidelity still reviews the WHOLE draft every turn (no narrowing)

We explicitly **rejected** scoping the per-turn fidelity review to only the changed
rewrites. Fidelity is the cheap (Haiku) leg, so narrowing it saves ~$0.005/turn while
opening a safety hole: a claim flagged on turn 1 that is not re-touched on turn 2 would
escape review, and the persisted verdict would describe a delta rather than the full
resume the user exports. The reviewer continues to run on the full rewrite set every
turn, skipped only when there are no rewrites (ADR-066 invariant intact). The cost
reduction comes from **fewer turns** (D), not from reviewing less.

### F. PDF export renders clean text

`render_pdf` is fixed on all three counts: the contact line and flat skills list join
with a literal middle-dot and pass RAW text to `P()` (escaped once); `ListItem`s drop
the `value="bullet"` override so the `ListFlowable`'s `•` glyph is used; and a new
`_pdf_safe()` (called from `_esc`, and on the grouped-skills path) maps common
non-CP1252 punctuation to ASCII and folds/drops anything still outside CP1252 so the
PDF never emits a notdef box. CP1252 already covers smart quotes, en/em dashes, the
middle dot, and accented Latin, so those are preserved. The fix is PDF-only — md/txt/
html/docx are Unicode-native and already rendered correctly.

## Consequences

- The job-focused "Focus a job" flow now works end to end: chat edits show in the live
  preview, survive Save, and reach the export. Exports are clean (no duplicated
  bullets). The fidelity loop converges, so a clean draft typically takes ~2 turns
  instead of 5 — roughly a 60% cut in clinic-chat spend, quality-positive.
- The interactive chat keeps Sonnet (`agents.resume_chat`, overridable in Settings).
  Quality is the user-facing judgment on this surface and a downgrade risks more turns;
  we did not trade it for a few cents while quality was the complaint.
- **Deferred cost levers (documented, not built):** a diff-based chat protocol (agent
  emits only changed rewrites, server merges) would cut the ~$0.03/turn Sonnet *output*
  cost from re-emitting the full overhaul, but changes the `ResumeChatTurnResult`
  contract and the renderer merge — out of scope for a bug-fix ADR. A Haiku A/B on
  `resume_chat` (schema fidelity first, per the model-eval rule) is the other lever.

## PSSR

- **Performance:** fewer chat turns; renderer adds bounded token-overlap scoring per
  rewrite (small bullet lists). No new I/O.
- **Scalability:** unchanged; per-session, per-profile.
- **Security/Privacy:** PII seam intact — `prior_fidelity` carries only the reviewer's
  claim *text* (already agent-authored, resume-derived) plus status/recommendation;
  `redact_pii_for_llm` still gates the profile; `raw_text` stays on the sanctioned
  Fidelity path only. No new at-rest data.
- **Reliability:** fidelity still reviews the whole draft every turn; `set_decision` no
  longer clobbers edits; chat-turn LLM failure still persists the revision with null
  fidelity. Regression tests cover each fix.

## Tests

- `test_set_decision_without_payload_preserves_chat_edits` (repo) — (B).
- `test_rewrite_merging_two_bullets_collapses_without_duplication`,
  `test_rewrite_token_overlap_replaces_lightly_reworded_original` (renderer) — (C).
- `test_chat_feeds_prior_fidelity_into_agent_context`,
  `test_chat_reviews_full_draft_every_turn` (router) — (D), (E).
- `test_esc_single_escapes_and_does_not_double_escape`,
  `test_pdf_safe_maps_non_winansi_punctuation_to_ascii`,
  `test_render_pdf_text_has_no_literal_entities_or_bullet_word` (renderer) — (F).
